# -*- coding: utf-8 -*-
"""可靠启动器：后台启动 Flask 服务并强制打开浏览器。"""
import os
import sys
import time
import socket
import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HOST = "127.0.0.1"
PORT = 8978
URL = "http://%s:%d/" % (HOST, PORT)


def port_in_use():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect((HOST, PORT))
        s.close()
        return True
    except Exception:
        return False


def wait_ready(timeout=25):
    for _ in range(timeout * 5):
        if port_in_use():
            return True
        time.sleep(0.2)
    return False


def main():
    if not port_in_use():
        app_py = os.path.join(BASE_DIR, "app.py")
        flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        # sys.executable 即启动本脚本的解释器；若由 pythonw 启动则为 pythonw.exe（无窗口）
        subprocess.Popen([sys.executable, app_py], cwd=BASE_DIR, creationflags=flags)
        if not wait_ready():
            print("服务启动失败")
            return
    # 强制打开浏览器（比 webbrowser.open 更可靠）
    subprocess.Popen(["cmd", "/c", "start", "", URL], cwd=BASE_DIR)


if __name__ == "__main__":
    main()