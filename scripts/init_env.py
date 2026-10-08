#!/usr/bin/env python3
"""Create a safe first-install .env without overwriting an existing deployment."""
from pathlib import Path
import argparse
import os
import secrets

ROOT=Path(__file__).resolve().parents[1]


def initialize(path, *, template=None):
    path=Path(path)
    source=Path(template) if template is not None else ROOT/"env.example"
    content=source.read_text(encoding="utf8")
    values={"OKXQUANT_SETUP_TOKEN":secrets.token_urlsafe(24)+"A7",
            "OKXQUANT_AUTOTRADE_ENABLED":"0","OKXQUANT_GATEWAY_AUTOSTART":"1",
            "OKXQUANT_RUNTIME_PROFILE":"light","OKXQUANT_OKX_ENV":"demo","LLM_API_KEY":""}
    lines=[line for line in content.splitlines() if line.split("=",1)[0].strip() not in values]
    content="\n".join(lines+[key+"="+value for key,value in values.items()])+"\n"
    path.parent.mkdir(parents=True,exist_ok=True)
    # Exclusive creation also refuses an existing symlink. Never reset operator state.
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w",encoding="utf8",newline="\n") as handle:
        handle.write(content);handle.flush();os.fsync(handle.fileno())
    return path


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--path",type=Path,default=ROOT/".env")
    args=parser.parse_args()
    try:path=initialize(args.path)
    except FileExistsError:
        parser.error("Configuration already exists; refusing to change credentials or trading settings")
    print("Created "+str(path)+". Read its private OKXQUANT_SETUP_TOKEN locally for the first admin login; credentials are not printed.")
    print("Light monitoring is enabled. Automatic entries remain paused until explicitly enabled in the console.")

if __name__=="__main__":main()
