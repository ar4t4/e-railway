"""End-to-end UI checks against an isolated service. Never contacts the railway."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.chrome.service import Service
from companion.models import today

ROOT = Path(__file__).resolve().parent.parent


def main():
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="railway-ui-") as tmp:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        code = "import uvicorn; from companion.server import create_app; uvicorn.run(create_app(directory=" + repr(tmp) + ",background=False),host='127.0.0.1',port=" + str(port) + ",access_log=False)"
        env = {k:v for k,v in os.environ.items() if not k.startswith("RC_")}
        proc = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        driver = None
        try:
            url = f"http://127.0.0.1:{port}"
            for _ in range(50):
                try:
                    urllib.request.urlopen(url+"/healthz", timeout=1)
                    break
                except Exception:
                    time.sleep(.2)
            options = webdriver.ChromeOptions()
            options.add_argument("--headless=new")
            options.add_argument("--window-size=1440,1100")
            options.add_argument("--disable-gpu")
            options.set_capability("goog:loggingPrefs", {"browser":"ALL"})
            cached_driver = ROOT / '.runtime' / 'chromedriver' / 'chromedriver.exe'
            service = Service(str(cached_driver)) if cached_driver.is_file() else Service()
            driver = webdriver.Chrome(service=service, options=options)
            wait = WebDriverWait(driver, 15, ignored_exceptions=(StaleElementReferenceException,))
            driver.get(url)
            wait.until(EC.visibility_of_element_located((By.ID,"auth-form")))
            driver.save_screenshot(str(artifacts / "welcome.png"))
            driver.find_element(By.NAME,"password").send_keys("abc")
            driver.find_element(By.ID,"auth-submit").click()
            wait.until(EC.visibility_of_element_located((By.ID,"shell")))
            wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR,"[data-action='new']"))).click()
            dialog = driver.find_element(By.ID,"watch-form")
            dialog.find_element(By.NAME,"name").send_keys("Weekend in Sylhet")
            def choose(kind, query, label, keyboard=False):
                search = driver.find_element(By.ID, kind + '-search')
                search.clear()
                search.send_keys(query)
                option = wait.until(EC.visibility_of_element_located((By.XPATH, f"//*[@id='{kind}-options']/*[@role='option' and text()='{label}']")))
                if keyboard:
                    search.send_keys(Keys.ARROW_DOWN, Keys.ENTER)
                else:
                    option.click()
                assert search.get_attribute('value') == ''
            choose('origins', 'dhak', 'Dhaka', keyboard=True)
            choose('origins', 'biman band', 'Biman Bandar')
            choose('destinations', 'syl', 'Sylhet')
            choose('destinations', 'sree', 'Sreemangal')
            driver.find_element(By.CSS_SELECTOR, '[data-station-picker="destinations"] [aria-label="Remove Sreemangal"]').click()
            origin_search = driver.find_element(By.ID, 'origins-search')
            origin_search.send_keys('dhaka')
            assert not driver.find_elements(By.XPATH, '//*[@id="origins-options"]/*[@role="option" and text()="Dhaka"]'), 'Duplicate station offered'
            origin_search.clear()
            origin_search.send_keys(Keys.ESCAPE)
            assert driver.find_element(By.ID, 'watch-dialog').is_displayed(), 'Escape closed form instead of dropdown'
            assert [e.text for e in driver.find_elements(By.CSS_SELECTOR, '[data-station-picker="origins"] .station-chip>span')] == ['1. Dhaka', '2. Biman Bandar']
            driver.save_screenshot(str(artifacts / 'station-picker.png'))
            dialog.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
            error = wait.until(EC.visibility_of_element_located((By.ID, 'watch-error')))
            assert 'journey date' in error.text
            assert driver.execute_script("const e=document.querySelector('#watch-error'),r=e.getBoundingClientRect();return e.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))"), 'Save error is hidden behind the dialog'
            driver.save_screenshot(str(artifacts / 'watch-save-error.png'))
            # Choosing a date should suffice; Save must not require clicking Add first.
            driver.execute_script("document.querySelector('#date-input').value=arguments[0]", today())
            driver.execute_script("document.querySelector('#class-choices').scrollIntoView({block:'center'})")
            dialog.find_element(By.CSS_SELECTOR,"input[value='S_CHAIR']").click()
            dialog.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
            wait.until(lambda d:'seat class' in d.find_element(By.ID,'watch-error').text)
            driver.execute_script("document.querySelector('#class-choices').scrollIntoView({block:'center'})")
            dialog.find_element(By.CSS_SELECTOR,"input[value='SNIGDHA']").click()
            passengers = dialog.find_element(By.NAME,"passengers")
            passengers.clear()
            passengers.send_keys("2")
            # A rejected save must leave the draft intact and the button usable for retry.
            driver.execute_script("""const original=window.fetch;window.fetch=async(...args)=>{
                if(args[0]==='/api/watchlists'&&args[1]?.method==='POST'){
                    window.fetch=original;return new Response(JSON.stringify({detail:'Temporary save failure. Please retry.'}),{status:503,headers:{'Content-Type':'application/json'}});
                }return original(...args);
            };""")
            dialog.find_element(By.CSS_SELECTOR,"button[type='submit']").click()
            wait.until(lambda d:'Temporary save failure' in d.find_element(By.ID,'watch-error').text)
            assert dialog.find_element(By.NAME, 'name').get_attribute('value') == 'Weekend in Sylhet'
            dialog.find_element(By.CSS_SELECTOR,"button[type='submit']").click()
            wait.until(EC.invisibility_of_element_located((By.ID,"watch-dialog")))
            wait.until(lambda d:"Weekend in Sylhet" in d.find_element(By.ID,"content").text)
            driver.save_screenshot(str(artifacts / "dashboard.png"))
            # Persisted configuration survives a reload.
            driver.refresh()
            wait.until(lambda d:"Weekend in Sylhet" in d.find_element(By.ID,"content").text)
            saved = driver.execute_async_script("const done=arguments[0];fetch('/api/state').then(r=>r.json()).then(s=>done(s.watches[0]));")
            assert saved['origins'] == ['Dhaka', 'Biman_Bandar'] and saved['destinations'] == ['Sylhet']
            assert saved['dates'] == [today()] and saved['classes'] == ['SNIGDHA']
            driver.find_element(By.CSS_SELECTOR, '[data-action="edit"]').click()
            wait.until(EC.visibility_of_element_located((By.ID, 'watch-dialog')))
            assert [e.text for e in driver.find_elements(By.CSS_SELECTOR, '[data-station-picker="origins"] .station-chip>span')] == ['1. Dhaka', '2. Biman Bandar']
            driver.find_element(By.CSS_SELECTOR, '#watch-dialog [data-close]').click()
            # At PC cannot be armed without a paired, ready desktop.
            driver.find_element(By.CSS_SELECTOR,"[data-mode='at_pc']").click()
            wait.until(lambda d:"Connect the desktop" in d.find_element(By.ID,"toast").text)
            driver.get(url+"/#settings")
            wait.until(EC.visibility_of_element_located((By.ID,"preferences-form")))
            assert driver.find_element(By.CSS_SELECTOR, '[data-action="normal-browser"]').text == 'Connect Railway'
            assert not driver.find_element(By.CSS_SELECTOR, '[data-action="pair"]').is_displayed()
            interval = driver.find_element(By.CSS_SELECTOR,"#preferences-form [name='interval']")
            interval.clear()
            interval.send_keys("25")
            driver.find_element(By.CSS_SELECTOR,"#preferences-form button[type='submit']").click()
            wait.until(lambda d:d.find_element(By.CSS_SELECTOR,"#preferences-form [name='interval']").get_attribute("value")=="25")
            driver.save_screenshot(str(artifacts / "settings.png"))
            session_input = driver.find_element(By.CSS_SELECTOR, '#session-form [name=session]')
            session_save = driver.find_element(By.CSS_SELECTOR, '#session-form button[type=submit]')
            session_input.send_keys('copy(JSON.stringify({token:localStorage.token}))')
            session_save.click()
            wait.until(lambda d:'Console command, not its copied result' in d.find_element(By.ID,'session-status').text)
            session_input.clear()
            session_input.send_keys('undefined')
            session_save.click()
            wait.until(lambda d:'No session JSON' in d.find_element(By.ID,'session-status').text)
            session_input.clear()
            session_input.send_keys('{}')
            session_save.click()
            wait.until(lambda d:'Missing token and uudid' in d.find_element(By.ID,'session-status').text)
            driver.save_screenshot(str(artifacts / 'session-save-error.png'))
            session_input.clear()
            session_input.send_keys(json.dumps(json.dumps({'token':'ui-fixture-token-only', 'uudid':'ui-fixture-device-only', 'ssdk':None})))
            session_save.click()
            wait.until(lambda d:'Session saved securely' in d.find_element(By.ID,'session-status').text)
            assert session_input.get_attribute('value') == '', 'Session credentials left visible after saving'
            assert 'Saved' in driver.find_element(By.ID,'railway-session-badge').text
            public = driver.execute_async_script("const done=arguments[0];fetch('/api/state').then(r=>r.text()).then(done)")
            assert 'ui-fixture-token-only' not in public and json.loads(public)['connections']['railway']
            driver.set_window_size(390,844)
            driver.get(url+"/#overview")
            wait.until(lambda d:"Weekend in Sylhet" in d.find_element(By.ID,"content").text)
            assert driver.execute_script("return document.documentElement.scrollWidth <= innerWidth"), "Mobile page overflows"
            driver.save_screenshot(str(artifacts / "mobile.png"))
            driver.execute_script("document.querySelector('[data-action=edit]').scrollIntoView({block:'center'})")
            driver.find_element(By.CSS_SELECTOR, '[data-action="edit"]').click()
            wait.until(EC.visibility_of_element_located((By.ID, 'watch-dialog')))
            driver.find_element(By.ID, 'origins-search').click()
            assert driver.execute_script("const d=document.querySelector('#watch-dialog');return d.scrollWidth<=d.clientWidth"), 'Station picker overflows mobile dialog'
            driver.save_screenshot(str(artifacts / 'station-picker-mobile.png'))
            errors = [e for e in driver.get_log("browser") if e["level"]=="SEVERE" and ("javascript" in e.get("source", "") or "Content Security Policy" in e["message"])]
            assert not errors, errors
            print(json.dumps({"status":"passed", "checks":["owner setup", "station search and keyboard selection", "ordered chips and removal", "duplicate prevention", "canonical station persistence and editing", "watchlist creation", "At PC readiness guard", "preferences", "mobile station picker", "JavaScript/CSP"],"screenshots":str(artifacts)}))
        finally:
            if driver:
                driver.quit()
            proc.terminate()
            proc.wait(timeout=10)


if __name__ == '__main__':
    main()
