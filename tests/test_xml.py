import pytest

from qlhd.core.xml_invoice import InvoiceXMLError, parse_invoice

SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<HDon xmlns="http://example.gdt/hddt"><DLHDon Id="data">
<TTChung><KHMSHDon>1</KHMSHDon><KHHDon>C26TAA</KHHDon><SHDon>0000123</SHDon><NLap>2026-03-01</NLap>
<DVTTe>VND</DVTTe></TTChung>
<NDHDon><NBan><Ten>Công ty Bán</Ten><MST>0101111111</MST></NBan>
<NMua><Ten>Công ty Mua</Ten><MST>0102222222</MST></NMua>
<TToan><THTTLTSuat><LTSuat><TSuat>10%</TSuat><ThTien>1000000</ThTien><TThue>100000</TThue></LTSuat></THTTLTSuat>
<TgTCThue>1000000</TgTCThue><TgTThue>100000</TgTThue><TgTTTBSo>1100000</TgTTTBSo></TToan></NDHDon>
</DLHDon></HDon>"""


def test_parse_namespaced_invoice():
    d = parse_invoice(SAMPLE.encode())
    assert d["SoHoaDon"] == "0000123" and d["KyHieuHD"] == "C26TAA" and d["KyHieuMauSo"] == "1"
    assert d["MSTNguoiBan"] == "0101111111" and d["TenNguoiMua"] == "Công ty Mua"
    assert (d["GiaTriTruocThue"], d["VAT"], d["GiaTriSauThue"]) == (1_000_000, 100_000, 1_100_000)
    assert d["ThueSuat"][0]["ThueSuat"] == "10%"


def test_bad_xml():
    with pytest.raises(InvoiceXMLError):
        parse_invoice(b"<a><b></a>")
    with pytest.raises(InvoiceXMLError):
        parse_invoice(b"<HDon><x/></HDon>")
