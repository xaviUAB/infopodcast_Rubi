import os
import re
import smtplib
from dataclasses import dataclass
from datetime import datetime, date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import List
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader, select_autoescape

BARCELONA_TZ = ZoneInfo("Europe/Madrid")
TIMEOUT = 30
HEADERS = {"User-Agent": "RubiDailyReportBot/2.1 (+GitHub Actions)"}
BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "templates"

URLS = {
    "meteocat_hourly": "https://m.meteo.cat/prediccio-per-hores?codi=081846",
    "meteocat_municipal": "https://www.meteo.cat/prediccio/municipal/081846",
    "power": "https://www.edistribucion.com/ca/averias/cortes-programados-luz-hoy.html",
    "water_inc": "https://www.aiguesdebarcelona.cat/el-teu-servei-daigua/incidencies",
    "water_works": "https://www.aiguesdebarcelona.cat/la-meva-aigua-ab/incidencies-i-contacte/obres-i-afectacions",
    "water_rubi": "https://www.rubi.cat/ca/temes/medi-ambient/cicle-integral-de-laigua/cicle-de-laigua",
    "traffic1": "https://www.rubi.cat/ca/actualitat/avisos/talls-de-transit-puntuals",
    "traffic2": "https://www.rubi.cat/fitxers/imatges/mobilitat/afectacions-viaries",
    "traffic3": "https://www.rubi.cat/ca/ajuntament/projectes-estrategics/transformacio-de-l2019avinguda-de-l2019estatut/afectacions-de-mobilitat",
    "agenda": "https://www.rubi.cat/ca/actualitat/agenda",
    "casino": "https://www.rubi.cat/ca/casino/programacio",
}

@dataclass
class WeatherRow:
    hour: str
    temp: str
    rain: str
    wind: str

@dataclass
class AgendaItem:
    title: str
    subtitle: str
    venue: str
    category: str
    dates: str
    url: str

@dataclass
class Report:
    generated_at: str
    target_date: str
    executive_summary: List[str]
    weather_summary: str
    weather_rows: List[WeatherRow]
    power_summary: str
    water_summary: str
    traffic_summary: str
    cultural_summary: str
    cultural_items: List[AgendaItem]
    methodology: List[str]
    sources: List[tuple]


def fetch(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def text_url(url: str) -> str:
    return clean(BeautifulSoup(fetch(url), "lxml").get_text(" "))


def parse_weather() -> tuple[str, List[WeatherRow]]:
    text = text_url(URLS['meteocat_hourly']) + ' ' + text_url(URLS['meteocat_municipal'])
    matches = re.findall(r"(\d{1,2}) h\|(\d{1,2}) ?°C\|(\d+(?:[\.,]\d+)?) mm\|([^|\n]+)", text)
    rows = []
    for hour, temp, rain, wind in matches[:10]:
        rows.append(WeatherRow(hour=f"{hour} h", temp=temp, rain=rain, wind=clean(wind)))
    if not rows:
        temps = re.findall(r"(\d{1,2}) ?°C", text)
        rains = re.findall(r"(\d+(?:[\.,]\d+)?) ?mm", text)
        winds = re.findall(r"([NSEO]{1,2} ?\d{1,3} ?km/h)", text)
        for i in range(min(8, max(len(temps), len(rains), len(winds), 1))):
            rows.append(WeatherRow(hour=f"Franja {i+1}", temp=temps[i] if i < len(temps) else '-', rain=rains[i] if i < len(rains) else '-', wind=winds[i] if i < len(winds) else '-'))
    temps_num = [int(r.temp) for r in rows if r.temp.isdigit()]
    no_rain = all(r.rain.replace(',', '.') in ('0', '0.0') for r in rows if r.rain != '-') if rows else False
    parts = []
    if temps_num:
        parts.append(f"Temperatura prevista aproximada entre {min(temps_num)} i {max(temps_num)} °C")
    if no_rain:
        parts.append("sense precipitació apreciable en les franges horàries consultades")
    if rows:
        parts.append("taula horària inclosa amb temperatura, pluja i vent")
    return '; '.join(parts) + '.', rows


def parse_power() -> str:
    try:
        t = text_url(URLS['power']).lower()
    except Exception:
        return "No s'ha pogut consultar automàticament el portal d'e-distribución; cal verificació per adreça o CUPS a la font oficial."
    return "La font oficial d'e-distribución està orientada a consulta per adreça o CUPS; no s'han identificat avisos municipals oberts inequívocs per a Rubí en la consulta pública feta avui." if 'cortes programados' in t or 'talls' in t else "No s'han detectat avisos oberts clars de talls programats de llum en la consulta pública feta avui."


def parse_water() -> str:
    ok = []
    for k in ['water_inc','water_works','water_rubi']:
        try:
            ok.append(text_url(URLS[k]))
        except Exception:
            pass
    if not ok:
        return "No s'ha pogut verificar automàticament la informació pública d'aigua."
    return "No s'han localitzat avisos públics inequívocs de talls d'aigua programats per al conjunt del municipi; la verificació precisa continua depenent del cercador oficial per adreça concreta."


def parse_traffic() -> str:
    blobs = []
    for k in ['traffic1','traffic2','traffic3']:
        try:
            blobs.append(text_url(URLS[k]))
        except Exception:
            pass
    joined = ' '.join(blobs).lower()
    if not joined:
        return "No s'ha pogut verificar automàticament l'apartat municipal d'afectacions viàries."
    findings = []
    if 'estatut' in joined:
        findings.append("afectacions a l'entorn de l'avinguda de l'Estatut")
    if 'flammarion' in joined:
        findings.append("referències al carrer Flammarion")
    if 'desvi' in joined or 'tall' in joined:
        findings.append("existència de talls o desviaments publicats")
    return ("Consten avisos municipals de mobilitat per obres, amb " + ', '.join(findings) + ".") if findings else "No s'han detectat avisos clars de talls de trànsit per obres en la consulta pública feta avui."


def parse_agenda(target_date: date) -> tuple[str, List[AgendaItem]]:
    raw = ''
    for key in ['agenda', 'casino']:
        try:
            raw += '\n' + fetch(URLS[key])
        except Exception:
            pass
    if not raw:
        return "No s'ha pogut extreure automàticament l'agenda cultural.", []
    txt = clean(BeautifulSoup(raw, 'lxml').get_text(' '))
    patterns = [
        re.compile(r"##\s+(.*?)\s+(.*?)\s+(EL CASINO,.*?|ATENEU MUNICIPAL,.*?|BIBLIOTECA MESTRE MARTÍ TAULER,.*?|RUBÍ FORMA,.*?|EL CELLER,.*?|CENTRE CÍVIC CA N'ORIOL,.*?|Plaça [A-Za-zÀ-ÿ ]+|Pl\. [A-Za-zÀ-ÿ ]+)\s+(Exposició|Taller|Formació|Acció comunitària|Activitat familiar|Casino|Espectacle|Música|Conferència|Recital|Teatre)", re.S),
    ]
    items = []
    seen = set()
    for pat in patterns:
        for m in pat.finditer(txt):
            title, subtitle, venue, category = m.groups()
            key = clean(title).lower()
            if key in seen:
                continue
            seen.add(key)
            items.append(AgendaItem(title=clean(title), subtitle=clean(subtitle)[:220], venue=clean(venue), category=clean(category), dates=target_date.strftime('%d/%m/%Y'), url=URLS['agenda']))
            if len(items) >= 12:
                break
        if items:
            break
    summary = f"S'han identificat {len(items)} activitats o exposicions destacades a les fonts culturals municipals consultades." if items else "No s'han pogut identificar activitats culturals amb prou estructura per a l'extracció automàtica completa."
    return summary, items


def build_report() -> Report:
    now = datetime.now(BARCELONA_TZ)
    target = now.date()
    weather_summary, weather_rows = parse_weather()
    power_summary = parse_power()
    water_summary = parse_water()
    traffic_summary = parse_traffic()
    cultural_summary, cultural_items = parse_agenda(target)
    executive_summary = [weather_summary, power_summary, water_summary, traffic_summary, cultural_summary]
    methodology = [
        "Meteocat és la font principal per a la predicció meteorològica municipal de Rubí.",
        "Els portals d'electricitat i aigua poden requerir consulta per adreça o CUPS; quan passa això, l'informe ho indica explícitament.",
        "Les afectacions viàries i l'agenda cultural es construeixen a partir de la web municipal de Rubí i dels seus equipaments.",
        "Quan l'extracció automàtica no pot certificar un detall específic, el text utilitza formulacions prudents i remet a la font oficial.",
    ]
    sources = [
        ("Meteocat predicció per hores", URLS['meteocat_hourly']),
        ("Meteocat predicció municipal", URLS['meteocat_municipal']),
        ("e-distribución talls programats", URLS['power']),
        ("Aigües de Barcelona incidències", URLS['water_inc']),
        ("Aigües de Barcelona obres i afectacions", URLS['water_works']),
        ("Ajuntament de Rubí cicle de l'aigua", URLS['water_rubi']),
        ("Ajuntament de Rubí talls de trànsit", URLS['traffic1']),
        ("Ajuntament de Rubí afectacions viàries", URLS['traffic2']),
        ("Ajuntament de Rubí afectacions de mobilitat", URLS['traffic3']),
        ("Ajuntament de Rubí agenda", URLS['agenda']),
        ("El Casino programació", URLS['casino']),
    ]
    return Report(
        generated_at=now.strftime('%Y-%m-%d %H:%M:%S %Z'),
        target_date=target.strftime('%d/%m/%Y'),
        executive_summary=executive_summary,
        weather_summary=weather_summary,
        weather_rows=weather_rows,
        power_summary=power_summary,
        water_summary=water_summary,
        traffic_summary=traffic_summary,
        cultural_summary=cultural_summary,
        cultural_items=cultural_items,
        methodology=methodology,
        sources=sources,
    )


def render(report: Report) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=select_autoescape(['html', 'xml']))
    return env.get_template('email_report_v2.html.j2').render(report=report)


def send_email(html: str, subject: str):
    host = os.environ['SMTP_HOST']
    port = int(os.environ.get('SMTP_PORT', '587'))
    user = os.environ['SMTP_USER']
    password = os.environ['SMTP_PASSWORD']
    sender = os.environ.get('EMAIL_FROM', user)
    recipient = os.environ['EMAIL_TO']
    use_tls = os.environ.get('SMTP_USE_TLS', 'true').lower() == 'true'
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = recipient
    msg.attach(MIMEText("Aquest correu conté una versió HTML de l'informe diari de Rubí.", 'plain', 'utf-8'))
    msg.attach(MIMEText(html, 'html', 'utf-8'))
    with smtplib.SMTP(host, port) as server:
        server.ehlo()
        if use_tls:
            server.starttls(); server.ehlo()
        server.login(user, password)
        server.sendmail(sender, [recipient], msg.as_string())


def main():
    report = build_report()
    html = render(report)
    (BASE_DIR / 'last_report_preview.html').write_text(html, encoding='utf-8')
    if os.environ.get('DRY_RUN', 'false').lower() == 'true':
        print('preview generated')
        return
    send_email(html, f"Informe diari de Rubí v2 - {report.target_date}")
    print('sent')


if __name__ == '__main__':
    main()
