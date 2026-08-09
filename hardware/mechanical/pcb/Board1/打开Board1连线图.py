# -*- coding: utf-8 -*-
"""Brufik Board1 转接板连线图 — 本地 HTTP 启动器
双击运行：自动启动本地服务并打开浏览器。
说明：直接双击 Board1_tscircuit.html 也能看静态连线图和表格，
但 tscircuit 的 3D/PCB 视图需要本地 HTTP 服务（浏览器安全限制）。
"""
import http.server, socketserver, threading, webbrowser, os, sys

DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(DIR)

class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

def pick_port():
    for port in (8765, 8766, 8767, 8770, 9001):
        try:
            srv = socketserver.TCPServer(("127.0.0.1", port), Quiet)
            srv.server_close()
            return port
        except OSError:
            continue
    return 8765

port = pick_port()
url = "http://127.0.0.1:%d/Board1_tscircuit.html" % port
threading.Timer(1.0, lambda: webbrowser.open(url)).start()
print("Brufik Board1 连线图已启动：" + url)
print("浏览器会自动打开；看完直接关闭本窗口即可退出服务。")
class S(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
with S(("127.0.0.1", port), Quiet) as httpd:
    httpd.serve_forever()
