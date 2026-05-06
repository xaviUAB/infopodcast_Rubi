# Informe diari de Rubí v2

Paquet preparat per a un repositori de GitHub amb GitHub Actions i enviament per SMTP.

## Què incorpora la versió 2

- Predicció meteorològica de Rubí basada en Meteocat.
- Bloc de talls programats de llum amb interpretació prudent de la consulta pública d'e-distribución.
- Bloc de talls d'aigua basat en Aigües de Barcelona i la informació municipal del cicle de l'aigua.
- Bloc de mobilitat i obres basat en avisos i afectacions viàries de l'Ajuntament de Rubí.
- Bloc d'activitat cultural i agenda municipal.
- Resum executiu i apartat de metodologia/cauteles.
- Previsualització local del correu en `last_report_preview.html`.

## Estructura

- `report.py`: script principal.
- `templates/email_report_v2.html.j2`: plantilla HTML del correu.
- `.github/workflows/daily-report.yml`: execució programada i manual.
- `requirements.txt`: dependències.

## Secrets a GitHub

Configura aquests secrets a `Settings > Secrets and variables > Actions`:

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASSWORD`
- `SMTP_USE_TLS`
- `EMAIL_FROM`
- `EMAIL_TO`

## Execució programada

El workflow està preparat per executar-se diàriament a les 07:00 hora de Barcelona mitjançant el camp `timezone` del `schedule` de GitHub Actions. Si el teu entorn GitHub no acceptés encara aquest camp, fes servir temporalment un cron en UTC i ajust manual estacional.

## Prova local

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
DRY_RUN=true python report.py
```

## Desplegament

1. Crea un repositori nou a GitHub.
2. Copia tots els fitxers d'aquest paquet a l'arrel del repositori.
3. Configura els secrets d'Actions.
4. Llança `workflow_dispatch` des de la pestanya **Actions**.
5. Verifica el correu rebut i, si cal, ajusta els parsers de les fonts.

## Límit metodològic

Algunes fonts oficials no ofereixen un llistat municipal estable o obert per a totes les incidències, especialment quan la verificació es fa per adreça o CUPS. En aquests casos, l'informe explicita la limitació i evita afirmar més del que la font pública permet verificar.
