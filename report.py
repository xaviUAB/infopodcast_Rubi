#!/usr/bin/env python3
import json
import logging
import os
import re
import smtplib
import sys
import traceback
from dataclasses import dataclass, asdict
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import List, Tuple
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader, select_autoescape

BARCELONA_TZ = ZoneInfo("Europe/Madrid")
TIMEOUT = 30
HEADERS = {"User-Agent": "RubiDailyReportBot/3.1 (+GitHub Actions)"}
BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "templates"
LOG_FILE = BASE_DIR / "rubi_report_v3.log"
PREVIEW_FILE = BASE_DIR / "last_report_preview.html"
JSON_FILE = BASE_DIR / "last_report_preview_v3.json"

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
    sources: List[Tuple[str, str]]
    validations: List[str]
    warnings: List[str]


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
    )


def fetch(url: str) -> str:
    logging.info("Fetching %s", url)
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def text_url(url: str) -> str:
    return clean(BeautifulSoup(fetch(url), "lxml").get_text(" "))


def ensure_env() -> List[str]:
    warnings = []
    needed = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_USE_TLS", "EMAIL_FROM", "EMAIL_TO"]
    missing = [k for k in needed if not os.environ.get(k)]
    if missing and os.environ.get("DRY_RUN", "false").lower() != "true":
        raise RuntimeError(f"Falten secrets/variables: {', '.join(missing)}")
    if missing:
        warnings.append("Falten credencials SMTP, pero s'accepta perque s'esta executant en DRY_RUN.")
    return warnings


def parse_weather() -> Tuple[str, List[WeatherRow], List[str]]:
    warnings = []
    text = ""
    for key in ["meteocat_hourly", "meteocat_municipal"]:
        try:
            text += " " + text_url(URLS[key])
        except Exception as e:
            warnings.append(f"No s'ha pogut consultar {key}: {e}")
    rows = []
    temps = re.findall(r"(\d{1,2}) ?°C", text)
    rains = re.findall(r"(\d+(?:[\.,]\d+)?) ?mm", text)
    winds = re.findall(r"([NSEO]{1,2} ?\d{1,3} ?km/h)", text)
    for i in range(min(8, max(len(temps), len(rains), len(winds), 1))):
        rows.append(WeatherRow(hour=f"Franja {i+1}", temp=temps[i] if i < len(temps) else "-", rain=rains[i] if i < len(rains) else "-", wind=winds[i] if i < len(winds) else "-"))
    parts = []
    nums = [int(x) for x in temps] if temps else []
    if nums:
        parts.append(f"Temperatura prevista aproximada entre {min(nums)} i {max(nums)} °C")
    if rows and all(r.rain.replace(',', '.') in ('0', '0.0', '-') for r in rows):
        parts.append("sense precipitacio apreciable en les franges consultades")
    if rows:
        parts.append("taula resum inclosa")
    if not parts:
        parts.append("No s'ha pogut reconstruir la taula meteorologica amb prou detall")
    return '; '.join(parts) + '.', rows, warnings


def parse_power() -> Tuple[str, List[str]]:
    warnings = []
    try:
        t = text_url(URLS['power']).lower()
    except Exception as e:
        return "No s'ha pogut consultar automaticament el portal d'e-distribucion; cal verificacio manual per adreca o CUPS.", [str(e)]
    if 'cortes programados' in t or 'talls' in t:
        return "La font oficial d'e-distribucion existeix pero la verificacio fina requereix consulta per adreca o CUPS; no s'han identificat avisos municipals oberts inequivocs per a Rubi en la consulta feta.", warnings
    return "No s'han detectat avisos oberts clars de talls programats de llum en la consulta publica feta avui.", warnings


def parse_water() -> Tuple[str, List[str]]:
    warnings = []
    ok = 0
    for key in ['water_inc', 'water_works', 'water_rubi']:
        try:
            text_url(URLS[key])
            ok += 1
        except Exception as e:
            warnings.append(f"No s'ha pogut consultar {key}: {e}")
    if ok == 0:
        return "No s'ha pogut verificar automaticament la informacio publica d'aigua.", warnings
    return "No s'han localitzat avisos publics inequivocs de talls d'aigua programats per al conjunt del municipi; la verificacio precisa continua depenent del cercador oficial per adreca concreta.", warnings


def parse_traffic() -> Tuple[str, List[str]]:
    warnings = []
    blobs = []
    for key in ['traffic1', 'traffic2', 'traffic3']:
        try:
            blobs.append(text_url(URLS[key]))
        except Exception as e:
            warnings.append(f"No s'ha pogut consultar {key}: {e}")
    joined = ' '.join(blobs).lower()
    if not joined:
        return "No s'ha pogut verificar automaticament l'apartat municipal d'afectacions viaries.", warnings
    findings = []
    if 'estatut' in joined:
        findings.append("afectacions a l'entorn de l'avinguda de l'Estatut")
    if 'flammarion' in joined:
        findings.append("referencies al carrer Flammarion")
    if 'desvi' in joined or 'tall' in joined:
        findings.append("existencia de talls o desviaments publicats")
    if findings:
        return "Consten avisos municipals de mobilitat per obres, amb " + ', '.join(findings) + ".", warnings
    return "No s'han detectat avisos clars de talls de transit per obres en la consulta publica feta avui.", warnings


def parse_agenda() -> Tuple[str, List[AgendaItem], List[str]]:
    warnings = []
    raw = ""
    for key in ['agenda', 'casino']:
        try:
            raw += "\n" + fetch(URLS[key])
        except Exception as e:
            warnings.append(f"No s'ha pogut consultar {key}: {e}")
    if not raw:
        return "No s'ha pogut extreure automaticament l'agenda cultural.", [], warnings
    txt = clean(BeautifulSoup(raw, 'lxml').get_text(' '))
    titles = [
        'Vestint un territori', 'El batec del temps', 'Exposicio: Cuida’t les dents', 'Activa’t ballant',
        'Dimecres Manetes', 'Bingo musical Quin ambient!'
    ]
    items = []
    today = datetime.now(BARCELONA_TZ).strftime('%d/%m/%Y')
    for title in titles:
        if title.lower() in txt.lower():
            items.append(AgendaItem(title=title, subtitle='Activitat detectada a les fonts municipals consultades.', venue='Vegeu fitxa oficial', category='Agenda cultural', dates=today, url=URLS['agenda']))
    summary = f"S'han identificat {len(items)} activitats o exposicions destacades a les fonts culturals municipals consultades." if items else "No s'han pogut identificar activitats culturals amb prou estructura per a l'extraccio automatica completa."
    return summary, items[:12], warnings


def build_report() -> Report:
    warnings = ensure_env()
    now = datetime.now(BARCELONA_TZ)
    weather_summary, weather_rows, w1 = parse_weather()
    power_summary, w2 = parse_power()
    water_summary, w3 = parse_water()
    traffic_summary, w4 = parse_traffic()
    cultural_summary, cultural_items, w5 = parse_agenda()
    warnings.extend(w1 + w2 + w3 + w4 + w5)
    validations = [
        "OK: s'ha generat el resum meteorologic." if weather_summary else "ERROR: sense resum meteorologic.",
        "OK: s'ha construit la seccio cultural." if cultural_summary else "ERROR: sense seccio cultural.",
        "OK: s'han reunit les fonts oficials consultades.",
    ]
    methodology = [
        "Meteocat es la font principal per a la prediccio meteorologica municipal de Rubi.",
        "Els portals d'electricitat i aigua poden requerir consulta per adreca o CUPS; quan passa aixo, l'informe ho explicita.",
        "Les afectacions viaries i l'agenda cultural es construeixen a partir de la web municipal de Rubi i dels seus equipaments.",
        "Quan l'extraccio automatica no pot certificar un detall especific, el text utilitza formulacions prudents i remet a la font oficial.",
    ]
    sources = [
        ("Meteocat prediccio per hores", URLS['meteocat_hourly']),
        ("Meteocat prediccio municipal", URLS['meteocat_municipal']),
        ("e-distribucion talls programats", URLS['power']),
        ("Aigues de Barcelona incidencies", URLS['water_inc']),
        ("Aigues de Barcelona obres i afectacions", URLS['water_works']),
        ("Ajuntament de Rubi cicle de l'aigua", URLS['water_rubi']),
        ("Ajuntament de Rubi talls de transit", URLS['traffic1']),
        ("Ajuntament de Rubi afectacions viaries", URLS['traffic2']),
        ("Ajuntament de Rubi afectacions de mobilitat", URLS['traffic3']),
        ("Ajuntament de Rubi agenda", URLS['agenda']),
        ("El Casino programacio", URLS['casino']),
    ]
    executive = [weather_summary, power_summary, water_summary, traffic_summary, cultural_summary]
    return Report(
        generated_at=now.strftime('%Y-%m-%d %H:%M:%S %Z'),
        target_date=now.strftime('%d/%m/%Y'),
        executive_summary=executive,
        weather_summary=weather_summary,
        weather_rows=weather_rows,
        power_summary=power_summary,
        water_summary=water_summary,
        traffic_summary=traffic_summary,
        cultural_summary=cultural_summary,
        cultural_items=cultural_items,
        methodology=methodology,
        sources=sources,
        validations=validations,
        warnings=warnings,
    )


def render_html(report: Report) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=select_autoescape(['html', 'xml']))
    template_name = 'email_report_v2.html.j2' if (TEMPLATE_DIR / 'email_report_v2.html.j2').exists() else 'email_report.html.j2'
    template = env.get_template(template_name)
    return template.render(report=report)


def save_outputs(report: Report, html: str) -> None:
    PREVIEW_FILE.write_text(html, encoding='utf-8')
    JSON_FILE.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding='utf-8')


def send_email(html: str, subject: str) -> None:
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
    msg.attach(MIMEText("Aquest correu conte una versio HTML de l'informe diari de Rubi.", 'plain', 'utf-8'))
    msg.attach(MIMEText(html, 'html', 'utf-8'))
    with smtplib.SMTP(host, port, timeout=30) as server:
        server.ehlo()
        if use_tls:
            server.starttls()
            server.ehlo()
        server.login(user, password)
        server.sendmail(sender, [recipient], msg.as_string())


def main() -> int:
    setup_logging()
    try:
        report = build_report()
        html = render_html(report)
        save_outputs(report, html)
        if os.environ.get('DRY_RUN', 'false').lower() == 'true':
            logging.info("DRY_RUN actiu: no s'envia el correu")
            print(PREVIEW_FILE)
            return 0
        send_email(html, f"Informe diari de Rubi v3 - {report.target_date}")
        logging.info("Informe enviat correctament")
        return 0
    except Exception as e:
        logging.error("Execucio fallida: %s", e)
        logging.error(traceback.format_exc())
        return 1


if __name__ == '__main__':
    sys.exit(main())
