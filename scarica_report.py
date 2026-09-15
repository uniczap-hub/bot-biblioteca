"""
Script per scaricare automaticamente il report CSV da Sebina Next.

COSA FA, IN ORDINE:
1. Apre il browser e va sulla pagina di login di Sebina Next
2. Inserisce username e password (presi da credenziali.py) e clicca "Conferma"
3. Nella schermata "Biblioteca di Lavoro" seleziona "LAMETINO" e clicca "Conferma"
4. Va sulla pagina dei report
5. Cerca il link al file CSV più recente e lo scarica nella cartella "report_scaricati"

In ogni fase salva uno screenshot dentro "screenshot_controllo", cosi' se qualcosa
si blocca possiamo vedere esattamente a che punto e perche'.
"""

import re
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

try:
    from credenziali import USERNAME, PASSWORD
except ImportError:
    # Quando lo script gira su GitHub Actions non esiste il file credenziali.py:
    # le credenziali arrivano invece da variabili d'ambiente (i "Secrets" di GitHub).
    import os
    USERNAME = os.environ.get("SEBINA_USERNAME")
    PASSWORD = os.environ.get("SEBINA_PASSWORD")
    if not USERNAME or not PASSWORD:
        raise Exception(
            "Credenziali non trovate: manca il file credenziali.py oppure le "
            "variabili d'ambiente SEBINA_USERNAME e SEBINA_PASSWORD."
        )

URL_LOGIN = "http://gestione.bibliotechecalabria.it/sebina"
URL_REPORT = "http://gestione.bibliotechecalabria.it/sebina/spooler/S_SPO_DIS_GES.do"
NOME_BIBLIOTECA_DA_CERCARE = "LAMETINO"

CARTELLA_SCRIPT = Path(__file__).parent
CARTELLA_SCREENSHOT = CARTELLA_SCRIPT / "screenshot_controllo"
CARTELLA_REPORT = CARTELLA_SCRIPT / "report_scaricati"


def log(messaggio):
    """Stampa un messaggio con l'orario davanti, cosi' si segue cosa sta succedendo."""
    ora = datetime.now().strftime("%H:%M:%S")
    print(f"[{ora}] {messaggio}")


def screenshot(pagina, nome):
    """Salva uno screenshot della pagina corrente per poterlo controllare dopo."""
    CARTELLA_SCREENSHOT.mkdir(exist_ok=True)
    percorso = CARTELLA_SCREENSHOT / nome
    pagina.screenshot(path=str(percorso))
    log(f"Screenshot salvato: {percorso}")


def seleziona_biblioteca(pagina):
    """
    Seleziona la biblioteca "LAMETINO" nel menu a tendina.

    Il <select> di questa pagina e' presente nel codice della pagina ma Playwright
    lo considera "non visibile" (probabilmente e' nascosto via CSS e sostituito da
    un widget grafico). Per questo NON possiamo usare select_option normale:
    proviamo prima con force=True, e se anche questo fallisce usiamo un metodo
    di riserva che imposta il valore direttamente via JavaScript.
    """
    select = pagina.locator("select").first

    # Troviamo il testo esatto dell'opzione che contiene "LAMETINO"
    opzioni = select.locator("option").all_inner_texts()
    opzione_lametino = next((o for o in opzioni if NOME_BIBLIOTECA_DA_CERCARE in o.upper()), None)

    if opzione_lametino is None:
        raise Exception(
            f"Non ho trovato nessuna opzione contenente '{NOME_BIBLIOTECA_DA_CERCARE}' nel menu. "
            f"Opzioni trovate: {opzioni}"
        )

    log(f"Seleziono: {opzione_lametino.strip()}")

    # --- TENTATIVO 1: select_option normale ma "forzato" ---
    try:
        select.select_option(label=opzione_lametino, force=True, timeout=5000)
        log("Selezione riuscita (metodo 1: force=True).")
        return
    except PlaywrightTimeoutError:
        log("Metodo 1 fallito, provo il metodo di riserva (JavaScript)...")

    # --- TENTATIVO 2 (di riserva): impostiamo il valore via JavaScript ---
    select.evaluate(
        """(el, testoCercato) => {
            let trovato = false;
            for (const opt of el.options) {
                if (opt.text.toUpperCase().includes(testoCercato)) {
                    el.value = opt.value;
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    trovato = true;
                    break;
                }
            }
            if (!trovato) { throw new Error('Opzione non trovata via JS'); }
        }""",
        NOME_BIBLIOTECA_DA_CERCARE,
    )
    log("Selezione riuscita (metodo 2: JavaScript diretto).")


def trova_riga_csv_piu_recente(pagina):
    """
    Cerca nella tabella dei report la riga il cui "Formato" contiene "Csv"
    (es. "Csv (campi separati da tab)") e restituisce la riga con la data
    di esecuzione piu' recente, nel caso ce ne siano piu' di una.

    Il link nella colonna "Descrizione" non ha un indirizzo CSV pronto
    nell'href: cliccandolo il sito genera il download al volo. Per questo
    non cerchiamo un indirizzo, ma la riga da cliccare.
    """
    righe = pagina.locator("table tr")
    numero_righe = righe.count()

    migliore_riga = None
    migliore_data = None

    for i in range(numero_righe):
        riga = righe.nth(i)
        testo_riga = riga.inner_text()

        if "Csv" not in testo_riga:
            continue
        if riga.locator("a").count() == 0:
            continue

        match = re.search(r"(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2})", testo_riga)
        if match:
            data = datetime.strptime(match.group(1), "%d/%m/%Y %H:%M:%S")
        else:
            data = datetime.min

        if migliore_data is None or data > migliore_data:
            migliore_data = data
            migliore_riga = riga

    if migliore_riga is None:
        raise Exception(
            "Non ho trovato nessuna riga con formato CSV nella tabella dei report. "
            "Controlla lo screenshot per capire come si presenta la pagina."
        )

    log(f"Riga CSV piu' recente trovata (data esecuzione: {migliore_data}).")
    return migliore_riga


def scarica_csv(pagina, riga):
    """
    Clicca sul link della riga e intercetta il download che il sito avvia,
    salvandolo nella cartella "report_scaricati".
    """
    CARTELLA_REPORT.mkdir(exist_ok=True)

    link = riga.locator("a").first

    with pagina.expect_download(timeout=30000) as download_info:
        link.click()
    download = download_info.value

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    nome_file = f"{timestamp}_{download.suggested_filename}"
    percorso_file = CARTELLA_REPORT / nome_file

    download.save_as(str(percorso_file))
    log(f"File CSV scaricato con successo: {percorso_file}")

    # Salviamo ANCHE una copia con nome fisso "catalogo_attuale.csv": il bot
    # sul sito web leggerà sempre questo stesso indirizzo, invece di dover
    # cercare ogni volta il nome del file più recente.
    percorso_fisso = CARTELLA_REPORT / "catalogo_attuale.csv"
    percorso_fisso.write_bytes(percorso_file.read_bytes())
    log(f"Copia con nome fisso salvata: {percorso_fisso}")

    return percorso_file


def main():
    with sync_playwright() as p:
        log("Apro il browser...")
        # headless=True: il browser gira "invisibile", senza aprire una finestra.
        # Necessario per far girare lo script su GitHub Actions (che non ha uno
        # schermo), e funziona benissimo anche sul tuo PC: per controllare cosa
        # succede si usano comunque gli screenshot salvati automaticamente.
        browser = p.chromium.launch(headless=True)
        pagina = browser.new_page()

        try:
            log("Vado sulla pagina di Sebina Next...")
            pagina.goto(URL_LOGIN)
            screenshot(pagina, "01_pagina_iniziale.png")

            log("Cerco il campo username...")
            pagina.fill("#username", USERNAME)
            log("Cerco il campo password...")
            pagina.fill("#password", PASSWORD)
            screenshot(pagina, "02_prima_del_login.png")

            log("Clicco sul pulsante di accesso...")
            pagina.click("#bottonediconferma")
            pagina.wait_for_load_state("networkidle")
            screenshot(pagina, "03_dopo_il_login.png")
            log("Login completato (probabilmente). Controlla lo screenshot 03 per conferma.")

            # Schermata "Biblioteca di Lavoro" - compare quasi sempre dopo il login
            if pagina.locator("select").count() > 0:
                log("Trovato un menu di selezione biblioteca. Cerco 'LAMETINO'...")
                seleziona_biblioteca(pagina)
                screenshot(pagina, "04_biblioteca_selezionata.png")

                log("Clicco su Conferma per confermare la biblioteca...")
                pagina.click("button:has-text('Conferma')")
                pagina.wait_for_load_state("networkidle")
                screenshot(pagina, "05_dopo_conferma_biblioteca.png")
            else:
                log("Nessun menu di selezione biblioteca trovato, proseguo.")

            log("Vado sulla pagina dei report...")
            pagina.goto(URL_REPORT)
            pagina.wait_for_load_state("networkidle")
            screenshot(pagina, "06_pagina_report.png")

            riga_csv = trova_riga_csv_piu_recente(pagina)
            percorso_file = scarica_csv(pagina, riga_csv)

            log(f"FATTO. Report scaricato in: {percorso_file}")

        except Exception as errore:
            log(f"ERRORE: {errore}")
            screenshot(pagina, "errore_finale.png")
            log("Ho salvato uno screenshot del punto in cui si e' bloccato.")
            log("Manda questo errore e lo screenshot 'errore_finale.png' per proseguire.")
            sys.exit(1)

        finally:
            browser.close()
            log("Browser chiuso. Fine dello script.")


if __name__ == "__main__":
    main()
