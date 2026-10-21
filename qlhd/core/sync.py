"""Đồng bộ dữ liệu giữa các máy bằng gói .qlhd (USB, email, Zalo…).

Gói thường: dữ liệu hợp đồng, hóa đơn, thanh toán, danh mục (không bao giờ có thu chi).
Gói Giám đốc: thêm toàn bộ dữ liệu thu chi (vẫn mã hóa bằng chìa khóa dữ liệu thu chi) và các bản bọc
khóa, để máy Giám đốc thứ hai theo dõi song song. Chỉ Giám đốc xuất được, khi đã mở khóa Thu chi.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import crypto
from .db import SYNC_FIELDS, SYNCED_TABLES, Database, now_iso

PKG_MAGIC = b"QLHDSYNC1\n"
PKG_EXT = ".qlhd"

ST_NEW = "Mới"
ST_SAME = "Đã có, giống nhau"
ST_THEIRS = "Bên gửi mới hơn"
ST_MINE = "Máy này mới hơn"
ST_CONFLICT = "Xung đột"
ST_DUP = "Có thể trùng"
ST_DELETED = "Đã xóa ở bên gửi"

A_TAKE = "nhan"          # lấy theo bên gửi
A_KEEP = "giu"           # giữ dữ liệu máy này
A_MERGE = "gop"          # coi là cùng một bản ghi với bản ghi trùng ở máy này
A_BOTH = "giu_ca_hai"    # thêm thành bản ghi riêng
A_SKIP = "bo_qua"

ACTION_LABELS = {A_TAKE: "Lấy theo bên gửi", A_KEEP: "Giữ máy này", A_MERGE: "Gộp thành một",
                 A_BOTH: "Giữ cả hai", A_SKIP: "Bỏ qua"}

DEFAULT_ACTION = {ST_NEW: A_TAKE, ST_SAME: A_SKIP, ST_THEIRS: A_TAKE, ST_MINE: A_KEEP, ST_CONFLICT: None,
                  ST_DUP: A_MERGE, ST_DELETED: A_TAKE}

FOREIGN_KEYS = {
    "NhomDoiTacThanhVien": {"MaNhom": "NhomDoiTac", "MaDoiTac": "DoiTac"},
    "HopDong": {"MaLoaiHD": "LoaiHopDong", "MaDoiTac": "DoiTac"},
    "CongViec": {"MaHopDong": "HopDong"},
    "LichSuBanGiao": {"MaCongViec": "CongViec"},
    "LichSuNghiemThu": {"MaCongViec": "CongViec"},
    "HoaDonDauRa": {"MaHopDong": "HopDong"},
    "ThanhToanHopDong": {"MaHopDong": "HopDong", "MaHDDauRa": "HoaDonDauRa"},
    "HoaDonDauVao": {"MaHopDong": "HopDong"},
}

TABLE_LABELS = {
    "LoaiHopDong": "Loại hợp đồng", "DoiTac": "Đối tác", "NhomDoiTac": "Nhóm đối tác",
    "NhomDoiTacThanhVien": "Thành viên nhóm", "HopDong": "Hợp đồng", "CongViec": "Công việc",
    "LichSuBanGiao": "Bàn giao", "LichSuNghiemThu": "Nghiệm thu", "HoaDonDauRa": "Hóa đơn đầu ra",
    "ThanhToanHopDong": "Thanh toán", "HoaDonDauVao": "Hóa đơn đầu vào",
}


class SyncError(Exception):
    pass


def _low(s) -> str:
    return (s or "").strip().lower()


def natural_key(table: str, r: dict):
    if table == "LoaiHopDong":
        return _low(r["TenLoaiHD"])
    if table == "DoiTac":
        return ("mst", r["MaSoThue"].strip()) if r.get("MaSoThue") else ("ten", _low(r["TenDoiTac"]))
    if table == "NhomDoiTac":
        return _low(r["TenNhom"])
    if table == "NhomDoiTacThanhVien":
        return (r["MaNhom"], r["MaDoiTac"])
    if table == "HopDong":
        return (_low(r["SoHopDong"]), _low(r.get("MaThueDoiTac") or r["TenDoiTac"]))
    if table == "CongViec":
        return (r["MaHopDong"], _low(r["TenNoiDungCV"]))
    if table == "LichSuBanGiao":
        return (r["MaCongViec"], r["NgayBanGiao"], _low(r["NoiDungTL"]))
    if table == "LichSuNghiemThu":
        return (r["MaCongViec"], r["NgayNghiemThu"], r["KetQuaNT"])
    if table == "HoaDonDauRa":
        return (_low(r.get("KyHieuHD")), _low(r["SoHoaDon"]))
    if table == "ThanhToanHopDong":
        return (r["MaHopDong"], r["NgayThanhToan"], r["SoTien"], _low(r.get("SoThamChieuSK")))
    if table == "HoaDonDauVao":
        return (r["MaThueNCC"].strip(), _low(r.get("KyHieuHD")), _low(r["SoHoaDonGoc"]))
    return None


def describe(table: str, r: dict) -> str:
    t = TABLE_LABELS.get(table, table)
    if table == "LoaiHopDong":
        return f"{t}: {r['TenLoaiHD']}"
    if table == "DoiTac":
        return f"{t}: {r['TenDoiTac']}" + (f" (MST {r['MaSoThue']})" if r.get("MaSoThue") else "")
    if table == "NhomDoiTac":
        return f"{t}: {r['TenNhom']}"
    if table == "HopDong":
        return f"Hợp đồng {r['SoHopDong']} – {r['TenHopDong']}"
    if table == "CongViec":
        return f"{t}: {r['TenNoiDungCV']}"
    if table == "LichSuBanGiao":
        return f"{t} ngày {r['NgayBanGiao']}: {r['NoiDungTL']}"
    if table == "LichSuNghiemThu":
        return f"{t} ngày {r['NgayNghiemThu']}: {r['KetQuaNT']}"
    if table == "HoaDonDauRa":
        return f"{t} {r.get('KyHieuHD') or ''} số {r['SoHoaDon']}"
    if table == "ThanhToanHopDong":
        return f"{t} ngày {r['NgayThanhToan']}: {r['SoTien']:,}".replace(",", ".") + " đ"
    if table == "HoaDonDauVao":
        return f"{t} {r.get('KyHieuHD') or ''} số {r['SoHoaDonGoc']} – {r['TenNhaCungCap']}"
    return t


def business_fields(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in SYNC_FIELDS and k != "Ma"}


@dataclass
class Package:
    header: dict
    body: dict
    path: str = ""
    already_imported: bool = False

    @property
    def is_director(self) -> bool:
        return self.header.get("loai") == "giam_doc"


@dataclass
class SyncItem:
    table: str
    their_ma: str
    ma: str                      # mã sau khi ánh xạ sang máy này
    status: str
    action: str | None
    label: str
    group: str | None            # Mã hợp đồng để nhóm theo hợp đồng
    incoming: dict
    local: dict | None = None
    dup_ma: str | None = None
    diff: list[str] = field(default_factory=list)


class SyncService:
    def __init__(self, db: Database, data_dir: Path, accounts=None):
        self.db = db
        self.data_dir = Path(data_dir)
        self.accounts = accounts

    # ------------------------------------------------------------ thông tin
    def machine_name(self) -> str:
        r = self.db.one("SELECT TenMay FROM ThongTinMay WHERE MaMay = ?", (self.db.machine_id,))
        return self.db.setting("ten_may") or (r["TenMay"] if r else self.db.machine_id)

    def partners(self) -> list[dict]:
        rows = self.db.query("SELECT MaMayDoiTac, TenMayDoiTac, MAX(ThoiDiem) t FROM LichSuDongBo"
                             " GROUP BY MaMayDoiTac ORDER BY t DESC")
        return [{"MaMay": r["MaMayDoiTac"], "TenMay": r["TenMayDoiTac"], "LanCuoi": r["t"]} for r in rows]

    def history(self) -> list[sqlite3.Row]:
        return self.db.query("SELECT * FROM LichSuDongBo ORDER BY MaLan DESC")

    def _company(self) -> tuple[str, str]:
        lic_row = self.db.one("SELECT MaCongTy, TenCongTy FROM BanQuyen WHERE Id = 1")
        if not lic_row:
            raise SyncError("Phần mềm chưa được kích hoạt.")
        return lic_row["MaCongTy"], lic_row["TenCongTy"]

    # ------------------------------------------------------------ xuất gói
    def collect(self, scope: str, *, partner: str | None = None, contract_ids: list[str] | None = None,
                in_invoice_from: str | None = None, in_invoice_to: str | None = None) -> dict[str, list[dict]]:
        tables: dict[str, list[dict]] = {}
        if scope == "all":
            for t in SYNCED_TABLES:
                tables[t] = [dict(r) for r in self.db.query(f"SELECT * FROM {t}")]
        elif scope == "changes":
            since = None
            if partner:
                r = self.db.one("SELECT MAX(ThoiDiem) t FROM LichSuDongBo WHERE MaMayDoiTac = ? AND Chieu = 'Gửi'",
                                (partner,))
                since = r["t"]
            for t in SYNCED_TABLES:
                if since:
                    tables[t] = [dict(r) for r in self.db.query(f"SELECT * FROM {t} WHERE NgaySua > ?", (since,))]
                else:
                    tables[t] = [dict(r) for r in self.db.query(f"SELECT * FROM {t}")]
        elif scope == "contracts":
            ids = list(contract_ids or [])
            marks = ",".join("?" * len(ids)) or "NULL"
            q = lambda sql, p=(): [dict(r) for r in self.db.query(sql, p)]  # noqa: E731
            tables["HopDong"] = q(f"SELECT * FROM HopDong WHERE Ma IN ({marks})", ids)
            tables["CongViec"] = q(f"SELECT * FROM CongViec WHERE MaHopDong IN ({marks})", ids)
            cv = [r["Ma"] for r in tables["CongViec"]]
            cvm = ",".join("?" * len(cv)) or "NULL"
            tables["LichSuBanGiao"] = q(f"SELECT * FROM LichSuBanGiao WHERE MaCongViec IN ({cvm})", cv)
            tables["LichSuNghiemThu"] = q(f"SELECT * FROM LichSuNghiemThu WHERE MaCongViec IN ({cvm})", cv)
            tables["HoaDonDauRa"] = q(f"SELECT * FROM HoaDonDauRa WHERE MaHopDong IN ({marks})", ids)
            tables["ThanhToanHopDong"] = q(f"SELECT * FROM ThanhToanHopDong WHERE MaHopDong IN ({marks})", ids)
            hdv = q(f"SELECT * FROM HoaDonDauVao WHERE MaHopDong IN ({marks})", ids)
            if in_invoice_from or in_invoice_to:
                hdv += q("SELECT * FROM HoaDonDauVao WHERE MaHopDong IS NULL AND NgayNhanHD BETWEEN ? AND ?",
                         (in_invoice_from or "0000-01-01", in_invoice_to or "9999-12-31"))
            tables["HoaDonDauVao"] = hdv
            partners = {r["MaDoiTac"] for r in tables["HopDong"] if r["MaDoiTac"]}
            tables["DoiTac"] = [dict(r) for r in self.db.query("SELECT * FROM DoiTac")
                                if r["Ma"] in partners or r["DaXoa"] == 0]
            for t in ("LoaiHopDong", "NhomDoiTac", "NhomDoiTacThanhVien"):
                tables[t] = q(f"SELECT * FROM {t}")
        else:
            raise SyncError("Phạm vi không hợp lệ.")
        # Danh mục loại hợp đồng luôn đi kèm để máy nhận hiểu mã loại.
        if scope != "all" and not tables.get("LoaiHopDong"):
            tables["LoaiHopDong"] = [dict(r) for r in self.db.query("SELECT * FROM LoaiHopDong")]
        return {t: tables.get(t, []) for t in SYNCED_TABLES}

    def export_package(self, path: str | Path, password: str, *, user, scope: str = "all",
                       partner: str | None = None, partner_name: str | None = None,
                       contract_ids: list[str] | None = None, in_invoice_from: str | None = None,
                       in_invoice_to: str | None = None, finance=None) -> dict:
        if len(password) < 6:
            raise SyncError("Mật khẩu đồng bộ phải có ít nhất 6 ký tự.")
        mst, cty = self._company()
        tables = self.collect(scope, partner=partner, contract_ids=contract_ids,
                              in_invoice_from=in_invoice_from, in_invoice_to=in_invoice_to)
        body: dict = {"tables": tables}
        if self.accounts is not None:
            tok = self.accounts.signed_revocations()
            if tok:
                body["revocations"] = tok
        loai = "thuong"
        if finance is not None:
            if user["VaiTro"] != "Giám đốc":
                raise SyncError("Chỉ Giám đốc được xuất Gói Giám đốc.")
            loai = "giam_doc"
            data = json.dumps(finance.export_rows(), ensure_ascii=False).encode()
            body["finance"] = {
                "wrappers": finance.wrappers(),
                "data": base64.b64encode(crypto.encrypt(finance.data_key, zlib.compress(data), b"qlhd-tc-pkg")).decode(),
            }
            body["ma_da_cap"] = [dict(r) for r in self.db.query("SELECT * FROM MaDaCap")]
        scope_text = {"all": "Toàn bộ dữ liệu", "changes": "Các thay đổi từ lần gửi trước",
                      "contracts": f"{len(contract_ids or [])} hợp đồng được chọn"}[scope]
        counts = {t: len(v) for t, v in tables.items() if v}
        salt = os.urandom(16)
        header = {"phien_ban": 1, "loai": loai, "may_gui": self.db.machine_id, "ten_may": self.machine_name(),
                  "nguoi_xuat": user["HoTen"], "thoi_diem": now_iso(), "pham_vi": scope_text,
                  "so_luong": counts, "mst": mst, "cong_ty": cty, "salt": salt.hex(), "kdf_iter": crypto.KDF_ITER}
        hbytes = json.dumps(header, ensure_ascii=False).encode()
        key = crypto.derive_key(password + "|" + mst, salt, crypto.KDF_ITER)
        blob = crypto.encrypt(key, zlib.compress(json.dumps(body, ensure_ascii=False).encode()), hbytes)
        path = Path(path)
        path.write_bytes(PKG_MAGIC + len(hbytes).to_bytes(4, "big") + hbytes + blob)
        self.db.execute(
            "INSERT INTO LichSuDongBo (MaMayDoiTac, TenMayDoiTac, Chieu, ThoiDiem, MocDuLieu, PhamVi, KetQua, TenDangNhap)"
            " VALUES (?, ?, 'Gửi', ?, ?, ?, ?, ?)",
            (partner or "", partner_name or "(chưa chọn máy nhận)", header["thoi_diem"], header["thoi_diem"],
             scope_text + (" + Thu chi (Gói Giám đốc)" if loai == "giam_doc" else ""),
             ", ".join(f"{TABLE_LABELS[t]}: {n}" for t, n in counts.items()), user["TenDangNhap"]))
        self.db.log(user["TenDangNhap"], "Xuất gói đồng bộ" + (" Giám đốc" if loai == "giam_doc" else ""), str(path))
        return header

    # ------------------------------------------------------------ đọc gói
    @staticmethod
    def read_header(path: str | Path) -> dict:
        raw = Path(path).read_bytes()
        if not raw.startswith(PKG_MAGIC):
            raise SyncError("File không phải gói đồng bộ của phần mềm.")
        n = int.from_bytes(raw[len(PKG_MAGIC):len(PKG_MAGIC) + 4], "big")
        return json.loads(raw[len(PKG_MAGIC) + 4:len(PKG_MAGIC) + 4 + n])

    def read_package(self, path: str | Path, password: str) -> Package:
        raw = Path(path).read_bytes()
        if not raw.startswith(PKG_MAGIC):
            raise SyncError("File không phải gói đồng bộ của phần mềm.")
        n = int.from_bytes(raw[len(PKG_MAGIC):len(PKG_MAGIC) + 4], "big")
        hbytes = raw[len(PKG_MAGIC) + 4:len(PKG_MAGIC) + 4 + n]
        header = json.loads(hbytes)
        mst, _ = self._company()
        if header["mst"] != mst:
            raise SyncError(f"Gói này của công ty khác ({header['cong_ty']}).")
        if header["may_gui"] == self.db.machine_id:
            raise SyncError("Gói này do chính máy này xuất ra.")
        key = crypto.derive_key(password + "|" + mst, bytes.fromhex(header["salt"]), header.get("kdf_iter", crypto.KDF_ITER))
        try:
            body = json.loads(zlib.decompress(crypto.decrypt(key, raw[len(PKG_MAGIC) + 4 + n:], hbytes)))
        except crypto.CryptoError:
            raise SyncError("Sai mật khẩu đồng bộ hoặc file đã bị hỏng / bị sửa.") from None
        seen = self.db.one("SELECT 1 FROM LichSuDongBo WHERE Chieu = 'Nhận' AND MaMayDoiTac = ? AND MocDuLieu = ?",
                           (header["may_gui"], header["thoi_diem"]))
        return Package(header, body, str(path), bool(seen))

    # ------------------------------------------------------------ so sánh
    def _id_map(self) -> dict[tuple[str, str], str]:
        return {(r["Bang"], r["MaNgoai"]): r["MaNoiBo"] for r in self.db.query("SELECT * FROM AnhXaMa")}

    def _last_receive(self, sender: str):
        return self.db.one("SELECT * FROM LichSuDongBo WHERE Chieu = 'Nhận' AND MaMayDoiTac = ?"
                           " ORDER BY MaLan DESC LIMIT 1", (sender,))

    def _remap(self, table: str, row: dict, id_map: dict) -> dict:
        r = dict(row)
        r["Ma"] = id_map.get((table, row["Ma"]), row["Ma"])
        for col, parent in FOREIGN_KEYS.get(table, {}).items():
            if r.get(col):
                r[col] = id_map.get((parent, r[col]), r[col])
        return r

    def _classify(self, pkg: Package, id_map: dict, decisions: dict | None = None,
                  prefer: str | None = None, apply_cb=None) -> list[SyncItem]:
        sender = pkg.header["may_gui"]
        last = self._last_receive(sender)
        their_mark = last["MocDuLieu"] if last else None
        local_mark = last["ThoiDiem"] if last else None
        items: list[SyncItem] = []
        cv_contract: dict[str, str] = {r["Ma"]: r["MaHopDong"] for r in self.db.query("SELECT Ma, MaHopDong FROM CongViec")}
        incoming_ids = {(t, r["Ma"]) for t, rows in pkg.body["tables"].items() for r in rows}
        for table in SYNCED_TABLES:
            rows = pkg.body["tables"].get(table, [])
            if not rows:
                continue
            nk_index: dict = {}
            for lr in self.db.query(f"SELECT * FROM {table} WHERE DaXoa = 0"):
                if (table, lr["Ma"]) in incoming_ids:
                    continue
                k = natural_key(table, dict(lr))
                if k is not None:
                    nk_index.setdefault(k, lr["Ma"])
            for row in rows:
                r = self._remap(table, row, id_map)
                local = self.db.get(table, r["Ma"])
                local = dict(local) if local else None
                dup_ma = None
                diff: list[str] = []
                if local is None:
                    if r["DaXoa"]:
                        status = ST_DELETED
                    else:
                        dup_ma = nk_index.get(natural_key(table, r))
                        status = ST_DUP if dup_ma else ST_NEW
                else:
                    bi, bl = business_fields(r), business_fields(local)
                    diff = [k for k in bi if bi.get(k) != bl.get(k)]
                    if not diff and r["DaXoa"] == local["DaXoa"]:
                        status = ST_SAME
                    elif r["DaXoa"] and not local["DaXoa"] and not (
                            local["MaMaySua"] != sender and local_mark and local["NgaySua"] > local_mark):
                        status = ST_DELETED
                    else:
                        if last is None:
                            theirs_changed = mine_changed = None
                        else:
                            theirs_changed = r["NgaySua"] > their_mark
                            mine_changed = local["MaMaySua"] != sender and local["NgaySua"] > local_mark
                        if theirs_changed is None:
                            status = ST_THEIRS if r["NgaySua"] >= local["NgaySua"] else ST_MINE
                        elif theirs_changed and mine_changed:
                            status = ST_CONFLICT
                        elif mine_changed:
                            status = ST_MINE
                        elif theirs_changed:
                            status = ST_THEIRS
                        else:
                            status = ST_THEIRS if r["NgaySua"] > local["NgaySua"] else ST_MINE
                if table == "HopDong":
                    group = r["Ma"]
                elif table in ("LichSuBanGiao", "LichSuNghiemThu"):
                    group = cv_contract.get(r["MaCongViec"])
                else:
                    group = r.get("MaHopDong")
                if table == "CongViec":
                    cv_contract[r["Ma"]] = r["MaHopDong"]
                    cv_contract[row["Ma"]] = r["MaHopDong"]
                action = (decisions or {}).get((table, row["Ma"]))
                if action is None or (status != ST_DUP and action in (A_MERGE, A_BOTH)):
                    action = DEFAULT_ACTION[status]
                if status == ST_CONFLICT and action is None and prefer:
                    action = A_TAKE if prefer == "gui" else A_KEEP
                item = SyncItem(table, row["Ma"], r["Ma"], status, action, describe(table, r), group, r, local,
                                dup_ma, diff)
                if status == ST_DUP and action == A_MERGE:
                    id_map[(table, row["Ma"])] = dup_ma
                    item.ma = dup_ma
                    if table == "CongViec":
                        cv_contract[row["Ma"]] = r["MaHopDong"]
                if apply_cb is not None:
                    apply_cb(item)
                items.append(item)
        return items

    def compare(self, pkg: Package) -> list[SyncItem]:
        return self._classify(pkg, self._id_map())

    # ------------------------------------------------------------ áp dụng
    def backup_dir(self) -> Path:
        d = self.data_dir / "SaoLuu"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def apply(self, pkg: Package, *, user, decisions: dict | None = None, prefer: str | None = None,
              only_groups: set | None = None) -> dict:
        """decisions: {(bảng, mã bên gửi): hành động}. only_groups: chỉ đồng bộ các hợp đồng đã chọn."""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = self.backup_dir() / f"QuanLyHopDong-truoc-dong-bo-{stamp}.db"
        self.db.backup_to(backup)
        result = {"them": 0, "cap_nhat": 0, "gop": 0, "bo_qua": 0, "chua_xu_ly": 0, "loi": 0, "loi_chi_tiet": []}
        id_map = self._id_map()

        def apply_item(it: SyncItem):
            in_scope = only_groups is None or it.group is None or it.group in only_groups
            if not in_scope or it.action in (None, A_SKIP, A_KEEP):
                if it.action is None and in_scope:
                    result["chua_xu_ly"] += 1
                elif it.status != ST_SAME:
                    result["bo_qua"] += 1
                if not in_scope and it.status == ST_DUP:
                    id_map.pop((it.table, it.their_ma), None)
                return
            if it.action == A_MERGE:
                self.db.conn.execute("INSERT OR REPLACE INTO AnhXaMa (Bang, MaNgoai, MaNoiBo) VALUES (?, ?, ?)",
                                     (it.table, it.their_ma, it.dup_ma))
                result["gop"] += 1
                return
            try:
                self.db.upsert_raw(it.table, it.incoming)
                result["them" if it.local is None else "cap_nhat"] += 1
            except sqlite3.IntegrityError as e:
                result["loi"] += 1
                result["loi_chi_tiet"].append(f"{it.label}: {e}")

        try:
            self._classify(pkg, id_map, decisions, prefer, apply_item)
            self.db.conn.commit()
        except Exception:
            self.db.conn.rollback()
            raise
        if pkg.body.get("revocations") and self.accounts is not None and self.accounts.is_activated():
            try:
                result["thu_hoi"] = self.accounts.apply_revocations(pkg.body["revocations"])
            except Exception as e:  # danh sách thu hồi lỗi không chặn việc đồng bộ
                result["loi_chi_tiet"].append(f"Danh sách thu hồi: {e}")
        for r in pkg.body.get("ma_da_cap", []):
            cols = list(r)
            self.db.conn.execute(f"INSERT OR REPLACE INTO MaDaCap ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                                 [r[c] for c in cols])
        h = pkg.header
        summary = (f"Thêm {result['them']}, cập nhật {result['cap_nhat']}, gộp {result['gop']}, "
                   f"bỏ qua {result['bo_qua']}, chưa xử lý {result['chua_xu_ly']}, lỗi {result['loi']}")
        self.db.conn.execute(
            "INSERT INTO LichSuDongBo (MaMayDoiTac, TenMayDoiTac, Chieu, ThoiDiem, MocDuLieu, PhamVi, KetQua, TenDangNhap)"
            " VALUES (?, ?, 'Nhận', ?, ?, ?, ?, ?)",
            (h["may_gui"], h["ten_may"], now_iso(), h["thoi_diem"], h["pham_vi"], summary, user["TenDangNhap"]))
        self.db.conn.commit()
        self.db.set_setting("dong_bo_hoan_tac", str(backup))
        self.db.log(user["TenDangNhap"], "Nhận gói đồng bộ", summary)
        result["sao_luu"] = str(backup)
        result["tom_tat"] = summary
        return result

    def can_undo(self) -> bool:
        p = self.db.setting("dong_bo_hoan_tac")
        return bool(p and Path(p).exists())

    def undo_last(self, user, finance_path: Path | None = None) -> None:
        p = self.db.setting("dong_bo_hoan_tac")
        if not p or not Path(p).exists():
            raise SyncError("Không có lần đồng bộ nào để hoàn tác.")
        fin_backup = self.db.setting("dong_bo_hoan_tac_tc")
        self.db.restore_from(p)
        self.db.set_setting("dong_bo_hoan_tac", None)
        if fin_backup and finance_path and Path(fin_backup).exists():
            shutil.copy2(fin_backup, finance_path)
        self.db.set_setting("dong_bo_hoan_tac_tc", None)
        self.db.log(user["TenDangNhap"], "Hoàn tác lần đồng bộ", p)

    # ------------------------------------------------------------ thu chi trong Gói Giám đốc
    def apply_finance(self, pkg: Package, store) -> dict:
        from .finance import FIN_TABLES

        fin = pkg.body.get("finance")
        if not fin:
            return {}
        try:
            raw = crypto.decrypt(store.data_key, base64.b64decode(fin["data"]), b"qlhd-tc-pkg")
        except crypto.CryptoError:
            raise SyncError("Dữ liệu thu chi trong gói dùng chìa khóa khác với thu chi trên máy này.") from None
        data = json.loads(zlib.decompress(raw))
        if store.path.exists():
            b = self.backup_dir() / f"ThuChi-truoc-dong-bo-{datetime.now().strftime('%Y%m%d-%H%M%S')}.dat"
            shutil.copy2(store.path, b)
            self.db.set_setting("dong_bo_hoan_tac_tc", str(b))
        fdb = store.fdb
        res = {"them": 0, "cap_nhat": 0, "xoa": 0}
        gone = {(t["Bang"], t["Ma"]): t["NgayXoa"] for t in data.get("tombstones", [])}
        for table in FIN_TABLES:
            for row in data["tables"].get(table, []):
                if (table, row["Ma"]) in gone:
                    continue
                local = fdb.get(table, row["Ma"])
                if fdb.one("SELECT 1 FROM XoaVinhVien WHERE Bang = ? AND Ma = ?", (table, row["Ma"])):
                    continue
                if local is None:
                    fdb.upsert_raw(table, row)
                    res["them"] += 1
                elif row["NgaySua"] > local["NgaySua"]:
                    fdb.upsert_raw(table, row)
                    res["cap_nhat"] += 1
        for (table, ma), ngay in gone.items():
            if fdb.get(table, ma) is not None:
                fdb.conn.execute(f"DELETE FROM {table} WHERE Ma = ?", (ma,))
                res["xoa"] += 1
            fdb.conn.execute("INSERT OR REPLACE INTO XoaVinhVien (Bang, Ma, NgayXoa) VALUES (?, ?, ?)", (table, ma, ngay))
        for k, v in data.get("settings", {}).items():
            if store.setting(k) is None:
                store.set_setting(k, v)
        fdb.conn.commit()
        store.save()
        return res
