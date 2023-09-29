
# Script qui sera lancé au démarrage de QGIS 
# Son chemin d'accès est à déclarer dans la variable d'environnement PYQGIS_STARTUP

import requests
import os

from datetime import datetime

def log(msg):
    return datetime.now().strftime("%d/%m/%Y %H:%M:%S") + " - " + msg

log_msg = []

appdata_path = os.getenv('APPDATA')
qgisdata_path = os.path.join(appdata_path, 'QGIS/QGIS3')

script_url = "https://sources-rec.lesagencesdeleau.eu/dsiun/dun/dode/sig/qgis-startup/-/raw/main/startup.py"
script_dest = os.path.join(qgisdata_path, 'startup.py')
log_dest = os.path.join(qgisdata_path, 'pyqgis_startup.log')

log_msg.append("Lecture du script source startup.py")
resp = requests.get(script_url, verify=False)
log_msg.append("Code de la requête HTTP : %s" % resp.status_code)

log_msg.append("Ecriture du fichier local startup.py")
with open(script_dest, "wb") as f:
    f.write(resp.content)

if not os.path.exists(log_dest):
    log_msg.append("Erreur - Le fichier local startup.py n'a pas été trouvé après écriture")

with open(log_dest, "w") as f:
    f.writelines('\n'.join(log_msg))