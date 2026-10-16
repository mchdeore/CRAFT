import re
import subprocess

import msgspec
from fastapi.testclient import TestClient

from app import create_app
from app.infrastructure.config.settings import Settings


def test_dev_disabled_is_not_found(settings) -> None:
    with TestClient(create_app(msgspec.structs.replace(settings, dev_page=False))) as client:
        assert client.get("/dev").status_code == 404
    enabled = msgspec.structs.replace(settings, dev_page=True)
    with TestClient(create_app(enabled)) as client:
        response = client.get("/dev")
        assert response.status_code == 200 and "text/html" in response.headers["content-type"]
        assert client.get("/dev/tests/test_dev_page.py").status_code == 404
    assert not Settings.from_env({"CRAFT_APP_DB_PASSWORD": "synthetic"}).dev_page
    assert Settings.from_env({"CRAFT_APP_DB_PASSWORD": "synthetic", "CRAFT_DEV_PAGE": "1"}).dev_page


def test_dev_page_same_origin_and_safe_rendering(settings) -> None:
    with TestClient(create_app(msgspec.structs.replace(settings, dev_page=True))) as client:
        html = client.get("/dev").text
    assert 'credentials: "include"' in html
    assert "localStorage" not in html and "sessionStorage" not in html
    assert "innerHTML" not in html and "__Host-craft_session" not in html
    match = re.search(r"<script>(.*?)</script>", html, re.S)
    assert match is not None
    script = match.group(1)
    harness = """
const vm = require("node:vm");
function element() { return { children: [], addEventListener(){},
  replaceChildren(){this.children=[]},
  append(child){this.children.push(child)}, set innerHTML(value){throw Error("unsafe rendering")},
  textContent: "", value: "", hidden: false }; }
const elements = new Map();
const document = {getElementById(id){
  if(!elements.has(id))elements.set(id,element());return elements.get(id)},
  createElement(){return element()}};
const context = vm.createContext({document,fetch:async()=>({ok:false,
  json:async()=>({message:"Not signed in."})})});
vm.runInContext(SOURCE,context);
vm.runInContext('renderHit({title:"<img src=x onerror=alert(1)>",'+
  'headingPath:"Test",text:"Synthetic"})',context);
const title = document.getElementById("results").children[0].children[0];
if(title.textContent!=="<img src=x onerror=alert(1)>"||title.children.length)process.exit(1);
""".replace("SOURCE", __import__("json").dumps(script))
    subprocess.run(["node", "-e", harness], check=True, capture_output=True)
