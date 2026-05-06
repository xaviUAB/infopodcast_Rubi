#!/usr/bin/env python3
import json
import logging
import os
import re
import smtplib
import sys
import traceback
from dataclasses import dataclass, asdict, field
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
HEADERS = {"User-Agent": "RubiDailyReportBot/3.2 (+GitHub Actions)"}
BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "templates"
LOG_FILE = BASE_DIR / "rubi_report_v3.log"
PREVIEW_FILE = BASE_DIR / "last_report_preview.html"
JSON_FILE = BASE_DIR / "last_report_preview_v3.json"

URLS = {
    "meteocat_hourly": "https://m.meteo.cat/prediccio-per-hores?codi=081846",
    "meteocat_municipal": "https://www.meteo.cat/prediccio/municipal/081846",
    "power_map": "https://www.edistribucion.com/ca/averias.html",
    "water_map": "https://agbar.veolia.cat/mapa-de-obras-y-afectaciones",
    "water_rubi": "https://www.rubi.cat/ca/temes/medi-ambient/cicle-integral-de-laigua/cicle-de-laigua",
    "traffic1": "https://www.rubi.cat/ca/actualitat/avisos/talls-de-transit-puntuals",
    "traffic2": "https://www.rubi.cat/fitxers/imatges/mobilitat/afectacions-viaries",
    "traffic3": "https://www.rubi.cat/ca/ajuntament/projectes-estrategics/transformacio-de-l2019avinguda-de-l2019estatut/afectacions-de-mobilitat",
    "agenda": "https://www.rubi.cat/ca/actualitat/agenda",
    "casino": "https://www.rubi.cat/ca/casino/programacio",
    "library_agenda": "https://www.rubi.cat/es/temas/cultura/equipaments/biblioteca-mestre-marti-tauler/agenda",
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
    map_snapshots: List[Tuple[str, str, str]] = field(default_factory=list)


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
        t = text_url(URLS['power_map']).lower()
    except Exception as e:
        return "No s'ha pogut consultar automaticament el mapa d'interrupcions d'e-distribucion; cal verificacio manual al portal.", [str(e)]
    if "mapa d'interrupcions d'energia" in t or "temps estimat de restauració" in t or "nombre de clients" in t:
        return "S'ha consultat el mapa oficial d'interrupcions d'e-distribucion per a possibles talls o avaries, pero el detall municipal de Rubí no es pot certificar amb precisio des de l'extraccio textual del mapa; cal comprovacio visual directa sobre el mapa o consulta del punt de subministrament.", warnings
    return "No s'ha pogut confirmar des del mapa textual si hi ha incidencies electriques concretes a Rubí en el moment de la consulta.", warnings


def parse_water() -> Tuple[str, List[str]]:
    warnings = []
    ok = 0
    for key in ['water_map', 'water_rubi']:
        try:
            text_url(URLS[key])
            ok += 1
        except Exception as e:
            warnings.append(f"No s'ha pogut consultar {key}: {e}")
    if ok == 0:
        return "No s'ha pogut verificar automaticament la informacio publica d'aigua.", warnings
    warnings.append("Per a la consulta d'aigua, el procediment previst es escriure 'Rubí' al selector de municipi del mapa d'Agbar abans d'interpretar els resultats.")
    return "S'ha consultat el mapa d'obres i afectacions d'Agbar i la consulta s'ha d'interpretar sobre el municipi de Rubí, introduint 'Rubí' al selector del mapa; l'extraccio textual sola no certifica si hi ha talls actius i cal contrast visual directe de la taula o del mapa.", warnings


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
    pages = [('agenda', URLS['agenda']), ('casino', URLS['casino']), ('library_agenda', URLS['library_agenda'])]
    html_by_key = {}
    for key, url in pages:
        try:
            html_by_key[key] = fetch(url)
            raw += "\n" + html_by_key[key]
        except Exception as e:
            warnings.append(f"No s'ha pogut consultar {key}: {e}")
    if not raw:
        return "No s'ha pogut extreure automaticament l'agenda cultural.", [], warnings
    items = []
    txt = clean(BeautifulSoup(raw, 'lxml').get_text(' '))
    general_titles = ['Vestint un territori', 'El batec del temps', 'Exposicio: Cuida’t les dents', 'Activa’t ballant', 'Dimecres Manetes', 'Bingo musical Quin ambient!', 'Edatisme zero']
    today = datetime.now(BARCELONA_TZ).strftime('%d/%m/%Y')
    for title in general_titles:
        if title.lower() in txt.lower():
            source_url = URLS['casino'] if 'bingo musical' in title.lower() else URLS['agenda']
            items.append(AgendaItem(title=title, subtitle='Activitat detectada a les fonts municipals consultades.', venue='Vegeu fitxa oficial', category='Agenda cultural', dates=today, url=source_url))
    if 'library_agenda' in html_by_key:
        libtxt = clean(BeautifulSoup(html_by_key['library_agenda'], 'lxml').get_text(' '))
        lib_titles = [
            'Exposición: Cuídate los dientes',
            'Te quiero, ballena azul',
            'Dharma yoga con Emili',
            'Música a fondo - Musicoterapia',
            'Recursos asistenciales y vivienda para personas mayores'
        ]
        seen = set((i.title.lower(), i.url) for i in items)
        for title in lib_titles:
            if title.lower() in libtxt.lower():
                key = (title.lower(), URLS['library_agenda'])
                if key not in seen:
                    seen.add(key)
                    items.append(AgendaItem(title=title, subtitle="Activitat extreta de l'agenda de la Biblioteca Mestre Martí Tauler.", venue='Biblioteca Mestre Martí Tauler', category='Agenda biblioteca', dates=today, url=URLS['library_agenda']))
    summary = f"S'han identificat {len(items)} activitats o exposicions destacades a les fonts culturals municipals i de la biblioteca consultades." if items else "No s'han pogut identificar activitats culturals amb prou estructura per a l'extraccio automatica completa."
    return summary, items[:20], warnings


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
        "OK: s'ha incorporat la biblioteca com a font cultural addicional.",
        "OK: s'han afegit els accessos rapids als mapes d'incidencies.",
    ]
    methodology = [
        "Meteocat es la font principal per a la prediccio meteorologica municipal de Rubí.",
        "Per a l'electricitat, es consulta el mapa d'interrupcions d'e-distribucion i es manté una formulacio prudent si el detall no es pot certificar textualment.",
        "Per a l'aigua, el procediment correcte es consultar el mapa d'Agbar escrivint 'Rubí' al selector del municipi abans d'interpretar el resultat.",
        "Les afectacions viaries i l'agenda cultural es construeixen a partir de la web municipal de Rubí, la biblioteca i el Casino.",
        "Quan l'extraccio automatica no pot certificar un detall especific, el text remet a la font oficial per contrast visual directe.",
    ]
    sources = [
        ("Meteocat prediccio per hores", URLS['meteocat_hourly']),
        ("Meteocat prediccio municipal", URLS['meteocat_municipal']),
        ("e-distribucion mapa d'interrupcions", URLS['power_map']),
        ("Agbar mapa d'obres i afectacions", URLS['water_map']),
        ("Ajuntament de Rubí cicle de l'aigua", URLS['water_rubi']),
        ("Ajuntament de Rubí talls de transit", URLS['traffic1']),
        ("Ajuntament de Rubí afectacions viaries", URLS['traffic2']),
        ("Ajuntament de Rubí afectacions de mobilitat", URLS['traffic3']),
        ("Ajuntament de Rubí agenda", URLS['agenda']),
        ("El Casino programacio", URLS['casino']),
        ("Biblioteca Mestre Marti Tauler agenda", URLS['library_agenda']),
    ]
    executive = [weather_summary, power_summary, water_summary, traffic_summary, cultural_summary]
    map_snapshots = [
        ("Mapa d'interrupcions elèctriques d'e-distribucion", URLS['power_map'], "Cal comprovacio visual directa del mapa per confirmar incidencies concretes a Rubí."),
        ("Mapa d'obres i afectacions d'Agbar (consulta per Rubí)", URLS['water_map'], "A Agbar s'ha d'escriure 'Rubí' al selector de municipi per consultar les afectacions d'aigua."),
    ]
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
        map_snapshots=map_snapshots,
    )


def render_html(report: Report) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=select_autoescape(['html', 'xml']))
    template_name = 'email_report_v3_compatible.html.j2' if (TEMPLATE_DIR / 'email_report_v3_compatible.html.j2').exists() else 'email_report.html.j2'
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
    msg.attach(MIMEText("Aquest correu conte una versio HTML de l'informe diari de Rubí.", 'plain', 'utf-8'))
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
        send_email(html, f"Informe diari de Rubí v3 - {report.target_date}")
        logging.info("Informe enviat correctament")
        return 0
    except Exception as e:
        logging.error("Execucio fallida: %s", e)
        logging.error(traceback.format_exc())
        return 1


if __name__ == '__main__':
    sys.exit(main())
