"""수집 엔진의 종목명 캐시가 다른 기능의 파일에 덮여도 죽지 않는지 (KeyError: 'stock' 회귀 시험)"""
import io
import json
import os
import sys
import tempfile
import types
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dart_engine  # noqa: E402
import score_auto  # noqa: E402

XML = """<?xml version="1.0"?><result><list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name><stock_code>005930</stock_code></list>
<list><corp_code>00000001</corp_code><corp_name>비상장사</corp_name><stock_code> </stock_code></list></result>"""


def _zip():
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("CORPCODE.xml", XML)
    return b.getvalue()


class Cache(unittest.TestCase):
    def test_score_auto_does_not_clobber_engine_cache(self):
        d = tempfile.mkdtemp()
        engine_file = os.path.join(d, "corp_names.json")
        json.dump({"stock": {"005930": "삼성전자"}, "corp": {"00126380": "삼성전자"}}, open(engine_file, "w", encoding="utf-8"), ensure_ascii=False)

        class R:
            status_code, content = 200, _zip()
        sess = types.SimpleNamespace(get=lambda *a, **k: R())
        score_auto._download_corp_map(d, "k", sess)
        self.assertEqual(json.load(open(engine_file, encoding="utf-8"))["stock"]["005930"], "삼성전자")      # 건드리지 않음
        self.assertEqual(score_auto.corp_code_by_name("삼성전자", d, "k", sess), "00126380")

    def test_engine_ignores_wrong_shaped_cache(self):
        d = tempfile.mkdtemp()
        json.dump({"삼성전자": "00126380"}, open(os.path.join(d, "corp_names.json"), "w", encoding="utf-8"), ensure_ascii=False)   # 예전 버그가 남긴 모양

        class R:
            content = _zip()
        stub = types.SimpleNamespace(cfg=types.SimpleNamespace(cache=d), log=lambda *a: None, get=lambda url: R())
        names = dart_engine.Engine.corp_names(stub)
        self.assertEqual(names["stock"]["005930"], "삼성전자")


if __name__ == "__main__":
    unittest.main()
