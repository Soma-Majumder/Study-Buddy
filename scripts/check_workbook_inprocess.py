"""Exercise the real workbook handlers/controllers without sockets or providers.

This is an API/DB rehearsal, not browser or real HTTP verification. Fixture
adapters are explicitly used by tests; this never touches the live workbook.
"""
import io
import json
import os
from email.message import Message
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from scripts.student_workbook import WorkbookServer,WorkbookHandler
from tests import test_student_workbook

registry={}
class Response:
    def __init__(self):self.status=None;self.body=b'';self.headers={}
    def read(self):return self.body
    def getheader(self,key,default=None):return self.headers.get(key.lower(),default)
class Connection:
    def __init__(self,host,port,timeout=None):self.server=registry[port]
    def close(self):pass
    def request(self,method,path,body=None,headers=None):
        response=Response();handler=WorkbookHandler.__new__(WorkbookHandler)
        handler.server=self.server;handler.path=path;handler.command=method
        handler.headers=Message();handler.headers['Host']=f'127.0.0.1:{self.server.server_port}'
        for key,value in (headers or {}).items():handler.headers[key]=value
        data=(body or '').encode() if isinstance(body,str) else body or b''
        handler.headers['Content-Length']=str(len(data));handler.rfile=io.BytesIO(data)
        handler.connection=type('ConnectionState',(),{'settimeout':lambda *args:None})()
        def reply(code,payload,kind='application/json; charset=utf-8'):
            response.status=code;response.body=payload if isinstance(payload,bytes) else json.dumps(payload).encode()
            response.headers['content-type']=kind
        handler.reply=reply;handler.send_response=lambda code:setattr(response,'status',code)
        handler.send_header=lambda key,value:response.headers.__setitem__(key.lower(),value)
        handler.end_headers=lambda:None
        getattr(handler,'do_'+method)();self.response=response
    def getresponse(self):return self.response

def init_no_socket(server,address,handler):
    server.server_port=20000+len(registry);registry[server.server_port]=server

def main():
    os.environ['STUDY_BUDDY_ALLOW_PAID_CLAUDE']='false'
    for name in ('ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN','OPENAI_API_KEY'):
        os.environ.pop(name,None)
    with patch('http.server.ThreadingHTTPServer.__init__',init_no_socket),patch.object(WorkbookServer,'serve_forever',lambda self:None),patch.object(WorkbookServer,'shutdown',lambda self:None),patch.object(WorkbookServer,'server_close',lambda self:None),patch('http.client.HTTPConnection',Connection):
        result=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromModule(test_student_workbook))
    print('Transport: in-process handler dispatch. No sockets, real providers, or live database.')
    return 0 if result.wasSuccessful() else 1
if __name__=='__main__':raise SystemExit(main())
