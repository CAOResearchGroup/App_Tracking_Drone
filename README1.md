# Kết nối drone qua Wi-Fi để đọc dữ liệu

Luồng kết nối: **PX4 → UART → module Wi-Fi → router → máy tính**.

Hướng dẫn cho một drone PX4, ID `1`. Module phải hỗ trợ **Station/Client** và cầu nối **UART MAVLink ↔ UDP hai chiều**; tên menu tùy model/firmware. Thử trên bàn, **tháo cánh quạt, không arm**.

## 1. Nối module với PX4

- Ngắt nguồn trước khi nối dây.
- Nối cổng TELEM của PX4 với UART module: **TX → RX, RX → TX, GND → GND**.
- Đối chiếu sơ đồ chân, điện áp nguồn và mức logic của đúng hai thiết bị; không đoán theo màu dây.

## 2. Cấu hình PX4

Kết nối PX4 với QGroundControl qua USB, vào **Parameters**:

- Đặt `MAV_SYS_ID = 1`.
- Chọn một instance MAVLink chưa sử dụng: đặt `MAV_n_CONFIG` vào đúng cổng TELEM đã nối, `MAV_n_MODE` là **Normal** (`n` là số instance).
- Đặt baud của cổng đó bằng baud UART module hỗ trợ, ví dụ **57600 ở cả hai đầu**. Với TELEM1/TELEM2, kiểm tra `SER_TEL1_BAUD`/`SER_TEL2_BAUD` tương ứng.
- Lưu, khởi động lại PX4 theo yêu cầu rồi đóng QGC.

Đối chiếu tên/tham số theo firmware tại [hướng dẫn cổng MAVLink PX4](https://docs.px4.io/main/en/peripherals/mavlink_peripherals).

## 3. Kết nối máy tính với router

- Kết nối máy tính vào router bằng Wi-Fi hoặc Ethernet; không cần Internet để nhận telemetry.
- Xem IP LAN của máy tính trong cài đặt mạng. Ví dụ: router `192.168.1.1`, máy tính `192.168.1.10`.
- Giữ IP máy tính ổn định bằng **DHCP reservation** trên router.
- Không dùng mạng khách/cách ly thiết bị (**AP/Client isolation**); cho phép ứng dụng nhận **UDP 14550** qua firewall. Không cần mở port ra Internet.

## 4. Cấu hình module Wi-Fi

Mở trang/công cụ cấu hình module theo hướng dẫn nhà sản xuất, đặt:

| Mục | Giá trị |
|---|---|
| Wi-Fi mode | Station / Client |
| SSID và mật khẩu | Wi-Fi của router |
| UART baud | Giống bước 2 |
| Giao thức truyền | UDP, chuyển tiếp MAVLink hai chiều |
| Destination / Remote IP | IP máy tính, ví dụ `192.168.1.10` |
| Destination / Remote port | `14550` |

Lưu và khởi động lại module. Kiểm tra module xuất hiện trong danh sách thiết bị của router. **IP đích là máy tính, không phải router và không phải `0.0.0.0`.**

## 5. Mở phần mềm nhận dữ liệu

Chạy trong Terminal; bỏ qua hai dòng setup nếu đã cài môi trường:

```sh
cd /Users/vungocson0503/Desktop/Python_Multi_Drone
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[mavlink,plots]'
.venv/bin/python run_cli.py --config config/px4.json --connect udpin:0.0.0.0:14550 --plots
```

Không thêm `--sim`. `0.0.0.0` ở đây là địa chỉ **lắng nghe trên máy tính**. Chỉ xem dữ liệu thì giữ `origin: null`, `frame_confirmed: false`; chưa cần cấu hình đội hình.

## 6. Kiểm tra dữ liệu

Đợi vài giây, nhập tại `fleet>`:

```text
list
status 1
watch 10
```

Phải thấy drone **ID 1** và dữ liệu cập nhật. Trong cửa sổ đồ thị, mở nhóm dưới Drone 1 rồi double-click tham số cần xem. Trường chưa có dữ liệu sẽ hiện `null`/`—`, không có nghĩa là mất toàn bộ kết nối.

Nếu thiếu dữ liệu, thử `streams 1` để yêu cầu PX4 gửi lại các luồng. Ứng dụng cũng tự yêu cầu luồng khi phát hiện drone; vì vậy module cần chuyển được gói ngược từ máy tính về PX4. Không cần arm/cất cánh để kiểm tra kết nối. Thoát bằng `quit`.

**Nếu không thấy drone:** kiểm tra lần lượt nguồn/dây UART → baud/cổng MAVLink → module đã vào router → IP máy tính/UDP 14550 → firewall/isolation. Nếu báo cổng đang bận, đóng QGC hoặc phiên CLI khác đang dùng cổng đó.
