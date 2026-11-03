# Auto Download - FB Photo

<img src="assets/icon_256.png" width="96" align="right">

Tool có giao diện để tải **toàn bộ ảnh** (bản lớn nhất) từ bài viết, album hoặc trang Facebook về máy.

## Tải về (bản portable)

👉 **[Tải `Auto-Download-FB-Photo.exe` ở mục Releases](https://github.com/laocuumon1982-cpu/Auto-Download-FB-Photo/releases/latest)**

Bạn chỉ cần một file `.exe`, không phải cài gì thêm. Copy file đi đâu cũng chạy được. Tool dùng Chrome hoặc Edge có sẵn trên máy, và phiên đăng nhập được lưu ngay cạnh file exe.

## Tính năng

- **Link bài viết / album / ảnh**: dán nhiều link một lúc, mỗi link lưu vào một thư mục.
- **Album của trang**: dán link trang, bấm *Lấy danh sách album*, tick các album muốn tải. Có thể chọn *Tất cả ảnh của trang*.
- **Chạy ẩn**: khi tải, tool không mở cửa sổ trình duyệt. Cửa sổ chỉ hiện lúc đăng nhập.
- **Tự khôi phục**: trình duyệt bị đóng giữa chừng thì tool mở lại và tải tiếp. Chạy lại cùng một link thì bỏ qua ảnh đã có.
- **Nút Cập nhật**: tool tự kiểm tra bản mới trên GitHub khi mở. Bấm một lần để tải, thay file và khởi động lại.

## Cách dùng

1. Bấm **Đăng nhập Facebook**, đăng nhập rồi đóng trình duyệt. Việc này chỉ cần làm một lần.
2. Dán link hoặc chọn album, rồi bấm **▶ Bắt đầu tải**.
3. Ảnh được lưu mặc định vào `Downloads/Facebook_Photos`.

Tool chỉ tải được ảnh mà tài khoản của bạn xem được.

## Chạy từ mã nguồn

- Windows: `run_windows.bat` · macOS: `bash run_mac.command` (cần Python 3.9+)
- Tự build exe: `build_portable_windows.bat`. Bản macOS `.app`: `build_app_mac.command`

## Phát hành bản mới (dành cho người phát triển)

1. Sửa `APP_VERSION` trong `auto_download_fb_photo.py`, ví dụ `"3.2"`.
2. Commit và push, rồi tạo tag: `git tag v3.2 && git push origin v3.2`.
3. GitHub Actions sẽ tự build `Auto-Download-FB-Photo.exe` và tạo Release. Tool của người dùng sẽ thấy bản mới khi mở lên.
