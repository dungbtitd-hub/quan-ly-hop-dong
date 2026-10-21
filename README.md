# Phần mềm Quản lý Hợp đồng

Phần mềm quản lý hợp đồng, hóa đơn đầu ra / đầu vào, thanh toán, thu chi bảo mật và đồng bộ giữa nhiều máy.
Chạy trên Windows và macOS. Nhà phát triển: **Công ty cổ phần Công nghệ ITD Việt Nam**
(Zalo 0988095867 · vanthu.itdvn@gmail.com · itdvietnam.com.vn · Số 1 Ngõ 30 đường Lý Sơn, Hà Nội).

## Tải bản cài đặt

Bản chính thức nằm ở trang **Releases** của kho mã:

| File | Dùng cho |
|---|---|
| `QuanLyHopDong-CaiDat-Windows-<phiên bản>.exe` | Bộ cài Windows 10/11 (64-bit): cài vào máy, tạo biểu tượng Desktop và Start menu, có gỡ cài đặt |
| `QuanLyHopDong-macOS-<phiên bản>.dmg` | MacBook chip Apple (M1 trở lên): mở file, kéo *QuanLyHopDong* vào *Applications* |

Phần mềm cấp Key của nhà phát triển (`CapMaBanQuyen-…-NOI-BO`) chỉ có ở tab **Actions** → lần chạy mới nhất →
mục *Artifacts*, **không** gửi cho khách hàng.

Bộ cài không cần quyền quản trị (cài cho người dùng hiện tại; muốn cài cho mọi người dùng thì chọn khi cài) và
không cần cài thêm Python hay thư viện nào. Gỡ cài đặt không xóa dữ liệu. GitHub tự cài thử bộ cài trên máy sạch,
chạy thử rồi gỡ ra ở mỗi lần build.

Phần mềm chưa ký số, nên lần đầu cài:
- Windows: nếu SmartScreen cảnh báo, bấm **More info** → **Run anyway**.
- macOS: mở ứng dụng, nếu bị chặn vào **System Settings → Privacy & Security** → **Open Anyway**.

Tạo trang Release: tab **Actions** → *Kiểm thử và đóng gói* → **Run workflow**, tích *Tạo trang Release*
(thẻ phiên bản lấy từ `qlhd/__init__.py`).

## Quy trình sử dụng

1. **Mỗi lần mở phần mềm**: hiện màn hình thông tin nhà phát triển và tình trạng bản quyền.
2. **Kích hoạt máy Giám đốc**: màn hình Kích hoạt hiện *Mã yêu cầu* → gửi cho nhà phát triển → nhận *Key cố định*
   (cấp bằng Phần mềm cấp mã) → dán Key, tạo tài khoản Giám đốc.
3. **Kích hoạt máy nhân viên**: nhân viên gửi *Mã máy* cho Giám đốc → Giám đốc vào **Tài khoản → Cấp mã kích hoạt**,
   chọn vai trò và quyền → gửi mã cho nhân viên → nhân viên dán mã, đặt mật khẩu.
   Kế toán viên không bao giờ được cấp quyền Thu chi.
4. **Máy Giám đốc thứ hai (theo dõi song song)**: cấp mã vai trò *Giám đốc* cho Mã máy thứ hai, cùng tên đăng nhập Giám đốc.
   Trên máy chính: **Đồng bộ → Xuất gói**, tích *Gói Giám đốc (kèm thu chi)*. Trên máy thứ hai: **Đồng bộ → Nhận gói**,
   nhập mật khẩu đồng bộ và Khóa thu chi.
5. **Đồng bộ máy kế toán ↔ máy Giám đốc**: xuất gói (toàn bộ / thay đổi từ lần gửi trước / hợp đồng tích chọn) →
   chuyển file `.qlhd` → nhận gói, xem từng dòng *Mới / Giống nhau / Bên gửi mới hơn / Máy này mới hơn / Xung đột /
   Có thể trùng / Đã xóa ở bên gửi* → đồng bộ tất cả hoặc từng hợp đồng. Có sao lưu tự động và hoàn tác.

## Chức năng chính

- Hợp đồng đầu ra / đầu vào, công việc, bàn giao, nghiệm thu, danh mục loại hợp đồng, đối tác, nhóm chủ đầu tư.
- Nhập hàng loạt hợp đồng, hóa đơn đầu ra, thanh toán từ **Excel mẫu** (nút *Tải file Excel mẫu*).
- Hóa đơn đầu ra và đầu vào đọc từ **XML** hoặc **PDF** hóa đơn điện tử (PDF có lớp chữ; bản scan nhập tay).
  Phần mềm tự gắn hóa đơn với hợp đồng đầu ra / đầu vào theo số hợp đồng ghi trên hóa đơn (kèm kiểm tra MST đối tác);
  không nhận ra được thì bắt người nhập tự chọn hợp đồng (có gợi ý), không điền sẵn để tránh gắn nhầm.
- Thanh toán nhiều lần cho mỗi hợp đồng / hóa đơn; tình trạng thu tiền tự tính.
- Báo cáo Excel theo **9 ngữ cảnh** (loại HĐ, chủ đầu tư, nhóm chủ đầu tư, hợp đồng, hóa đơn đã xuất, hợp đồng đã
  thanh toán, nhà cung cấp, hóa đơn đầu vào, thời gian) với **10 bộ lọc**, trong kỳ hoặc lũy kế, lưu mẫu báo cáo.
- **Thu chi bảo mật**: tệp `ThuChi.dat` mã hóa AES-256-GCM, mở bằng Khóa thu chi riêng (khác mật khẩu đăng nhập),
  chỉ Giám đốc và Người quản lý. Thu tự lấy từ thanh toán hợp đồng; chi theo dự án hoặc văn phòng, khoản mục nhiều cấp;
  tồn quỹ; báo cáo ngày / tháng / quý / năm, tổng hợp chi, thu - chi theo dự án; Excel có mật khẩu mở file;
  xóa theo khoảng thời gian hoặc toàn bộ; tự khóa; Mã khôi phục.
- Nhập hàng loạt **phiếu chi / phiếu thu khác từ Excel** (Thu chi → Sổ thu chi → *Nhập từ Excel*, chỉ khi đã mở khóa):
  file mẫu riêng, đọc được file Excel có mật khẩu, xem trước dòng lỗi / trùng, tự thêm khoản mục mới,
  tùy chọn xóa file Excel nguồn sau khi nhập.
- Sao lưu tự động khi thoát (giữ 30 bản), sao lưu / khôi phục thủ công.

## Dữ liệu nằm ở đâu

- Windows: `%APPDATA%\QuanLyHopDong`
- macOS: `~/Library/Application Support/QuanLyHopDong`

Gồm `QuanLyHopDong.db` (hợp đồng, hóa đơn, tài khoản), `ThuChi.dat` (thu chi, đã mã hóa), thư mục `SaoLuu`.

## Dành cho nhà phát triển

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q                                    # kiểm thử (đặt QT_QPA_PLATFORM=offscreen nếu không có màn hình)
python -m qlhd                                         # chạy phần mềm
python tools/license_manager.py                        # Phần mềm cấp mã (giao diện)
```

### Khóa bản quyền

Key Giám đốc được ký số Ed25519. Phần mềm chỉ chứa **khóa công khai** (`qlhd/core/vendor_pubkey.py`);
**khóa bí mật** nằm ở máy nhà phát triển, không bao giờ đưa lên kho mã (`*.key` đã nằm trong `.gitignore`).

Phần mềm đang dùng khóa **CHÍNH THỨC** (tạo 21/10/2026). Key Giám đốc cấp bằng khóa thử nghiệm (DEV) trước đó
không còn dùng được. Cấp Key: mở Phần mềm cấp mã (`CapMaBanQuyen`), chọn tệp `KHOA_BI_MAT_NHA_PHAT_TRIEN.key` (chỉ chọn một lần,
phần mềm nhớ; hoặc để tệp cạnh `CapMaBanQuyen` / trong thư mục Tài liệu), dán Mã yêu cầu của khách hàng.
Cặp khóa là **cố định**: phần mềm cấp mã chỉ nhận đúng tệp khóa khớp khóa công khai chính thức, không có chức năng
tạo cặp khóa mới.

Giữ tệp `.key` an toàn và có bản sao dự phòng ngoài máy: mất tệp này thì không cấp được Key mới (phải đổi khóa và
phát hành lại phần mềm); lộ tệp này thì người khác cấp được Key. Mọi Key đã cấp được ghi vào `NhatKyCapKey.csv`
cạnh tệp khóa. Chỉ khi buộc phải thay khóa mới chạy `python tools/license_manager.py init --write-app --thay-khoa-chinh-thuc` từ mã nguồn.

### Cấu trúc mã

```
qlhd/core/   licensing (Key, mã nhân viên, thu hồi) · auth (tài khoản, phân quyền) · db (SQLite + cột đồng bộ)
             contracts · finance (thu chi mã hóa) · reports (9 ngữ cảnh, Excel) · sync (gói .qlhd, Gói Giám đốc)
             xml_invoice · pdf_invoice · excel_import · crypto · machine
qlhd/ui/     giao diện PySide6 kiểu Windows 7
tools/       license_manager.py (Phần mềm cấp mã)
.github/     build.yml: kiểm thử + đóng gói Windows / macOS bằng PyInstaller
```
