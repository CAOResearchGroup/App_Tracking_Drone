# Từ cài đặt đến kết nối drone PX4

Chương trình có hai phần chạy cùng phiên: **CLI gửi lệnh/điều phối** và **cửa sổ telemetry vẽ đồ thị**. PX4 trên từng drone vẫn điều khiển động cơ và ổn định bay. Phần mềm trên máy tính tính/gửi setpoint cho follower; leader được người vận hành điều khiển riêng.

## 1. Setup một lần

Các lệnh dưới đây dành cho Terminal macOS/Linux, chạy tại thư mục dự án. Cần Python 3.11 trở lên.

```sh
cd /Users/vungocson0503/Desktop/Python_Multi_Drone
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[mavlink,plots]'
```

Nếu `.venv` đã có thì bỏ qua dòng tạo môi trường. Extra `mavlink` cài kết nối PX4; `plots` cài Qt/pyqtgraph để vẽ. Chỉ dùng CLI mô phỏng thì không cần cài hai extra. Windows dùng `.venv\Scripts\python.exe` thay `.venv/bin/python`.

## 2. Chạy thử không cần drone

```sh
.venv/bin/python run_cli.py --sim --plots
```

Terminal xuất hiện prompt `fleet>`; một cửa sổ đồ thị mở ra. Nhập các lệnh sau **trong prompt**, không phải một Terminal khác:

```text
list
arm all
takeoff all 3
wait 3
formation start
formation wait 5
sim move 1 3 0 -3
formation status
```

Đồ thị mặc định hiển thị `localPosition.x/y/z` của ba drone. Leader mô phỏng đi đến `[3, 0, -3]`; follower bám theo offset. Kết thúc:

```text
formation stop
land all
wait 3
list
quit
```

Demo tự động: `.venv/bin/python run_cli.py --sim --plots --script examples/demo.txt`. Cửa sổ đóng khi script kết thúc. Dùng phiên tương tác ở trên để tự thao tác đồ thị lâu hơn.

## 3. Chuẩn bị đường truyền thật

```text
PX4 leader   ↔ UART ↔ module Wi-Fi ─┐
PX4 follower ↔ UART ↔ module Wi-Fi ─┼─ router ↔ Wi-Fi/Ethernet ↔ máy tính
PX4 follower ↔ UART ↔ module Wi-Fi ─┘
```

Ví dụ địa chỉ, thay bằng mạng thực:

| Thiết bị | IP mạng LAN | MAVLink system ID |
|---|---|---|
| Router | 192.168.1.1 | — |
| Máy tính | 192.168.1.10 | 250 (GCS của ứng dụng) |
| Module của leader | 192.168.1.101 | PX4: 1 |
| Module của follower 1 | 192.168.1.102 | PX4: 2 |
| Module của follower 2 | 192.168.1.103 | PX4: 3 |

Trên PX4, đặt `MAV_SYS_ID` khác nhau cho từng drone, khớp `config/px4.json`; áp dụng/reboot theo firmware. Bật MAVLink trên đúng cổng nối module, ví dụ TELEM1/TELEM2, và đặt baud UART khớp với module. Tên instance `MAV_n_CONFIG`, baud và mode phụ thuộc cổng/firmware, không sao chép nguyên giá trị của drone khác. Tham khảo [cấu hình cổng MAVLink PX4](https://docs.px4.io/main/en/peripherals/mavlink_peripherals) và [MAV_SYS_ID](https://docs.px4.io/main/en/advanced_config/parameter_reference#MAV_SYS_ID).

Trên từng module Wi-Fi:

1. Chọn chế độ **station/client** và kết nối SSID của router. Firmware module phải hỗ trợ mô hình này và chuyển tiếp MAVLink hai chiều.
2. Đặt UDP destination/remote host là **IP máy tính**, ví dụ `192.168.1.10`, remote port **14550**. `0.0.0.0` chỉ dùng để ứng dụng lắng nghe, không phải địa chỉ đích của module.
3. Đảm bảo gói UDP chiều ngược được chuyển về UART/PX4. Adapter hiện trả tới địa chỉ/port nguồn học được từ gói đến; bridge phải nhận phản hồi tại endpoint đó hoặc có cơ chế định tuyến tương ứng.

Máy tính ở cùng LAN; firewall cho phép UDP 14550, router không bật client/AP isolation cho các thiết bị này. Nên giữ IP máy tính ổn định bằng DHCP reservation. Đóng link QGC đang chiếm cùng port; muốn chạy cả hai cần định tuyến riêng các endpoint. Xem [Wi-Fi telemetry của PX4](https://docs.px4.io/main/en/telemetry/telemetry_wifi). Màn hình cấu hình cụ thể của module phụ thuộc model/firmware.

## 4. Kết nối và xác nhận dữ liệu hai chiều

```sh
.venv/bin/python run_cli.py --config config/px4.json --connect udpin:0.0.0.0:14550 --plots
```

Đợi heartbeat, rồi nhập:

```text
list
status 1
status 2
params get 1 MAV_SYS_ID
params get 2 MAV_SYS_ID
streams all
```

`list` phải thấy đúng ID, mode, pin và tuổi dữ liệu cập nhật. `params get` có phản hồi là một cách xác nhận đường truyền lệnh/đáp ứng hai chiều. Trường nào PX4 chưa gửi sẽ hiện `null` trong CLI hoặc `—` trên đồ thị. Chưa có drone thì chương trình vẫn chờ; không tự arm/cất cánh.

**Chỉ xem telemetry/đồ thị không cần khai báo origin.** Có thể để `origin: null`, `frame_confirmed: false` như cấu hình gốc.

## 5. Chạy một drone và đội hình

Bản này đã test mô phỏng động học và MAVLink loopback; **chưa kiểm chứng PX4 SITL/HITL hoặc bay thật**. Cần kiểm chứng tích hợp trên firmware/bridge của bạn trước khi coi đây là phần mềm triển khai bay.

Vận hành một drone, tại thời điểm bạn chủ động cho phép bay:

```text
arm 1
takeoff 1 3
status 1
```

Đợi trạng thái `landed=2` (IN_AIR) và độ cao thực đạt mục tiêu; ACK của lệnh chỉ nói PX4 đã chấp nhận. Các lệnh đơn khác: `hold 1`, `land 1`, `rtl 1`. CLI hiện chưa có lệnh điều khiển joystick/goto thật; `sim move` chỉ dùng cho mô phỏng.

Đối với đội hình, sửa `config/px4.json` **trước khi khởi động phiên kết nối**:

- ID/vai trò đúng với các drone thực.
- `origin` là gốc local của từng drone trong NED chung, **không phải offset đội hình**: `p_common = p_local + origin`.
- `offset` là vị trí follower tương đối với leader, ví dụ `[-3,-2,0]` nghĩa là lệch -3 m North, -2 m East, cùng cao độ.
- Chỉ đặt `frame_confirmed: true` khi đã xác định các frame cùng hướng và đổi gốc đúng. Không điền tất cả `[0,0,0]` chỉ để vượt kiểm tra; local origin của từng PX4 có thể khác nhau.
- Chỉnh giới hạn độ cao/khoảng cách/tốc độ phù hợp. Bản hiện tại chỉ hỗ trợ frame cùng hướng, đổi gốc bằng tịnh tiến.

Đưa **từng drone** vào trạng thái armed/IN_AIR, vị trí/estimator/pin hợp lệ, rồi:

```text
formation setup 1 2 3
formation start
formation wait 5
formation status
```

Máy tính phát setpoint giữ vị trí trước, chuyển follower vào Offboard và bắt đầu bám leader khi xác nhận thành công. Vòng điều phối mặc định 20 Hz; leader không bị đổi mode. Người vận hành điều khiển leader bằng nguồn điều khiển đã thiết lập trên PX4. `formation status` cho biết sai số target so với vị trí follower.

Kiểm tra failsafe onboard cho mất Offboard/link theo [PX4 Offboard](https://docs.px4.io/main/en/flight_modes/offboard), nhất là `COM_OF_LOSS_T` và `COM_OBL_RC_ACT`. Khi máy tính mất nguồn, phần mềm không thể gửi lệnh thay PX4.

Kết thúc: `formation stop` để dừng bám, `land all` để yêu cầu hạ cánh, theo dõi `list/status` đến khi đã xuống đất, sau đó `quit`. **Đóng cửa sổ đồ thị không dừng bay; `formation stop` không đồng nghĩa hạ cánh.**

## 6. Dùng đồ thị thời gian thực

Từ CLI đang chạy:

```text
plot open
plot status
plot close
```

- Chọn drone ở cây bên trái, mở nhóm tham số. Double-click một trường để vẽ. **Mỗi cặp ID drone + tên tham số chỉ có trong một ô**, kể cả ô tự do hoặc đang nằm trong tab. Bấm lại tên tham số sẽ đưa ô đang chứa tham số lên trước, không tạo bản sao và không đặt lại mốc dữ liệu.
- **Làm mới** bắt đầu một lượt vẽ mới cho **riêng ô đó**: xóa phần vẽ cũ, đặt trục thời gian về 0 tại lúc bấm và chỉ hiển thị các mẫu nhận từ mốc này trở đi. Dữ liệu cũ còn trong hàng đợi cũng bị loại khỏi phần vẽ; ô sẽ trống nếu chưa có mẫu mới. Thao tác này không gửi lệnh đến drone, không xóa log và không đặt lại các ô khác.
- Nếu đang **Dừng hình**, Làm mới vẫn xóa phần vẽ và đặt mốc ngay; bỏ Dừng hình để xem các mẫu nhận sau mốc đó. Tách đường giữ mốc làm mới; khi gộp các ô có mốc khác nhau, ô gộp dùng mốc mới nhất để không đưa dữ liệu đã xóa trở lại.
- Tiêu đề ghi rõ **Drone ID 1: localPosition.x**. Ô nhiều đường hiển thị danh sách, ví dụ **Drone ID 2: localPosition.x, localPosition.y**; rê chuột trên ô để xem tooltip đầy đủ nếu tiêu đề bị cắt.
- Ctrl/Cmd hoặc Shift + click nhiều trường rồi chọn **Vẽ các tham số đã chọn**, hoặc double-click lựa chọn: các trường cùng drone và cùng đơn vị gộp vào một ô; khác đơn vị mở ô riêng. Nếu đã có ô chứa một trường trong lựa chọn, phần mềm dùng lại ô đó và chuyển các trường được chọn vào; các đường không được chọn vẫn ở ô cũ.
- Kéo trường từ cây vào **vùng thả** trên một đồ thị cùng drone, cùng đơn vị để thêm đường. Nếu trường đã ở ô khác, nó được **chuyển**, không sao chép. Ô nguồn còn các đường khác thì giữ lại; hết đường thì đóng. Khác đơn vị được mở/giữ ở ô riêng.
- Kéo nút **Kéo để gộp** của đồ thị A vào vùng thả của B để chuyển các đường cùng đơn vị sang B và đóng A khi hết đường. Hoặc bấm **Gộp cùng đơn vị** để gom các ô theo từng drone và đơn vị.
- Cột **Drone/Tham số** giữ cố định bên trái, không kéo đi nơi khác, không tách/đóng cột. Kéo **vạch ngăn dọc** giữa cột và vùng đồ thị để tăng/giảm chiều rộng; cột luôn cao bằng vùng làm việc.
- Mỗi đồ thị là **cửa sổ con trong vùng trống bên phải**. Kéo thanh tiêu đề để di chuyển. Để co giãn dễ hơn, kéo viền hai bên/dưới rộng **12 px**, hoặc tay kéo màu xanh **28×28 px** ở hai góc dưới; con trỏ đổi thành mũi tên co giãn khi trỏ đúng vùng. Cạnh trên và góc trên cũng kéo được, giữ chỗ cho các nút tiêu đề. Phóng to ô chỉ chiếm vùng bên phải, không che cột trái; các tay kéo ẩn khi phóng to hoặc xem tab. Có thể dùng thanh cuộn vùng đồ thị nếu các ô vượt kích thước vùng nhìn.
- **Xếp tab** gom các ô thành tab, **Xếp dọc** chuyển về các ô nối tiếp theo chiều dọc. Nút **Tách cửa sổ** đã bỏ vì trùng chức năng chuyển về ô của Xếp dọc.
- **Tách đồ thị…** mở danh sách để chọn một hoặc nhiều đại lượng. Mỗi đại lượng được chọn tách thành ô riêng; các đường chưa chọn vẫn chung ô. **Bỏ đường** xóa một đường; đóng một ô chỉ bỏ hiển thị ô đó. Hai nút này chỉ hiện khi ô có nhiều đường.
- Cùng đơn vị: vẽ chung trục Y, có màu/legend riêng. Khác đơn vị: các ô đồ thị riêng. Không trộn ID drone trên cùng đồ thị.
- Cuộn chuột hoặc **Zoom +/−** để phóng/thu cả hai trục; kéo chuột để pan. Bật **Khoanh zoom** rồi kéo khoanh vùng muốn phóng to; tắt nút để kéo pan trở lại. Zoom/pan tự giữ vùng nhìn của riêng ô, không bị cập nhật 10 Hz kéo trở lại. **Vừa dữ liệu** căn vừa lịch sử đang có; **Bám lại** tiếp tục bám dữ liệu mới và tự căn Y. Bật lại **Bám thời gian** để tất cả ô bám lại. Trục góc hiển thị trực tiếp độ, không tự đổi sang hệ số ×0.001.
- `vehicle.yaw` là tên mới của `vehicle.heading`, lấy từ ATTITUDE/ATTITUDE_QUATERNION và vẫn chuẩn hóa 0–360° để hiển thị; yaw dùng điều khiển vẫn giữ radian gốc. Sau cập nhật đổi tên này, thoát và chạy lại CLI để cả bộ nhận và đồ thị dùng cùng schema.
- **Dừng hình** chỉ dừng vẽ; vẫn nhận dữ liệu và điều khiển. Bỏ **Bám thời gian** để kéo/zoom, bật lại để trở về thời gian thực. Cửa sổ thời gian chọn 15/30/60/120 s.

Có thể chọn toàn bộ 52 trường telemetry; đây là các đại lượng đo/trạng thái, không phải mọi parameter cấu hình `params list`. Giá trị boolean vẽ 0/1; enum vẽ mã số; vị trí/setpoint hiển thị **local NED của chính drone**. Muốn so vị trí thực với mục tiêu autopilot trả về, kéo `localPosition.x` và `localPositionSetpoint.x` vào cùng ô; setpoint không được PX4 phát thì đường đó vẫn trống.

Hiển thị lấy snapshot tối đa 10 Hz, giữ lịch sử tối đa 120 s, có giới hạn số điểm. Đây là đồ thị giám sát được giảm mẫu, không phải bản ghi đủ mọi gói 20–50 Hz. Timestamp dùng thời điểm máy tính nhận mẫu; không phải đồng bộ clock giữa các drone. Mất/cũ dữ liệu thì ngắt nét, không điền số 0. Kết nối lại sẽ xóa lịch sử cũ; không lưu bố cục khi đóng cửa sổ ở phiên bản này. Log telemetry gốc tiếp tục nằm trong `logs/`.

Các cửa sổ con cùng dùng dữ liệu của phiên hiện tại, không tạo thêm kết nối MAVLink. Đóng cửa sổ đồ thị chính hoặc `plot close` sẽ đóng cả các cửa sổ con; CLI và điều khiển vẫn chạy. Sau khi cập nhật source, dùng `plot close` rồi `plot open` để tiến trình đồ thị nạp code mới, không cần khởi động lại kết nối drone.

Thay đổi này sửa lỗi tạo ô trùng khi bấm lại tham số; chưa xác định thông báo macOS `getApplicationProperty: called with invalid property` có cùng nguyên nhân. Không tự tắt/ẩn thông báo đó.

## 7. Lỗi thường gặp

| Hiện tượng | Kiểm tra |
|---|---|
| Không thấy drone | IP/port đích của module, UART/baud, MAVLink PX4 heartbeat, firewall và client isolation. |
| Thấy telemetry nhưng `params get` timeout | Bridge chưa chuyển chiều ngược, endpoint nhận phản hồi sai hoặc trùng ID. |
| `Address already in use` | QGC/phiên CLI khác đang giữ UDP 14550. |
| Đồ thị trống / `—` | Trường chưa được phát, giá trị unavailable hoặc quá tuổi; thử `status ID`, `streams ID` và xem ACK/log. |
| `Install plots first` | Cài extra `plots` bằng đúng Python đang chạy CLI; dùng `.venv/bin/python`. |
| Offboard/formation bị từ chối | Xem `formation status`: trạng thái bay, origin, estimator, tuổi telemetry, pin, giới hạn hoặc ACK PX4. |
| Không có desktop/display | Chạy CLI không `--plots`; cửa sổ Qt cần phiên desktop. |

Chi tiết các lệnh/API thuật toán: [cli.md](cli.md). Ý nghĩa/đơn vị/message của 52 trường: [telemetry-leader-follower.md](telemetry-leader-follower.md).
