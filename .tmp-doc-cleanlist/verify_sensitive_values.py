import re
import zipfile
from pathlib import Path


docx_path = Path(__file__).with_name("清单-内容整理版.docx")
with zipfile.ZipFile(docx_path) as archive:
    xml_text = "\n".join(
        archive.read(name).decode("utf-8", errors="ignore")
        for name in archive.namelist()
        if name.endswith(".xml")
    )

patterns = {
    "long_sk_key": r"sk-[A-Za-z0-9_-]{16,}",
    "jwt": r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    "google_api_key": r"AIza[A-Za-z0-9_-]{20,}",
    "private_key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
}

hits = {name: len(re.findall(pattern, xml_text)) for name, pattern in patterns.items()}
print(hits)
if any(hits.values()):
    raise SystemExit("Potential sensitive value found")
