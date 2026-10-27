# Python Multi-Drone

CLI Python điều phối **PX4 leader–follower theo mô hình tập trung**: leader gửi trạng thái qua Wi-Fi/router về máy tính; máy tính tạo giá trị đặt và gửi lại từng follower. Bộ điều khiển độc lập trên drone thực hiện ổn định bay và bám mục tiêu.

**v0.1:** CLI, mô phỏng tích hợp, adapter MAVLink 2 UDP/TCP, telemetry, parameter, command, đội hình, thuật toán Python, log/replay và cửa sổ đồ thị realtime kéo/thả. Chưa có GUI điều khiển bay hoặc chuyển file `.m`.

## Chạy nhanh

Python **3.11+**. Mô phỏng chạy bằng thư viện chuẩn, không cần MATLAB hoặc drone:

```sh
python3 run_cli.py --sim
```

Demo tự động ba drone — đọc/ghi parameter, cất cánh, bám leader rồi hạ cánh:

```sh
python3 run_cli.py --sim --script examples/demo.txt
```

Lệnh chính trong CLI:

```text
list
params get 2 MPC_XY_VEL_MAX
arm all
takeoff all 3
wait 3
formation start
formation wait 5
sim move 1 3 0 -3
watch 5
formation status
formation stop
land all
quit
```

Gõ `help` để xem đầy đủ lệnh. `all`, `leader`, `followers` hoặc `2,3` chọn phạm vi gửi lệnh; kết quả trả riêng từng drone.

## Đồ thị thời gian thực

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[mavlink,plots]'
.venv/bin/python run_cli.py --sim --plots
```

Đã có `.venv` thì bỏ qua bước tạo. CLI vẫn nhận lệnh trong khi cửa sổ đồ thị chạy. Có thể mở/đóng sau bằng `plot open`, `plot close`, xem `plot status`.

Mỗi cặp **drone ID + tham số** chỉ xuất hiện trong một ô: double-click lại sẽ làm mới và đưa ô hiện có lên trước, không mở bản sao. Tiêu đề ghi rõ, ví dụ **Drone ID 2: localPosition.x**; ô gộp liệt kê các tham số.

Cột **Drone/Tham số** cố định bên trái, chỉ đổi chiều rộng bằng kéo vạch ngăn dọc. Các ô Scope nằm trong vùng bên phải: kéo tiêu đề để di chuyển, kéo cạnh/góc để co giãn; không mở cửa sổ nổi ra ngoài hay che cột trái. **Xếp tab/Xếp dọc** chuyển chế độ và sắp xếp các ô.

**Làm mới** xóa phần vẽ cũ của riêng ô đang chọn, đặt thời gian về 0 và chỉ vẽ mẫu nhận từ lúc bấm; không xóa log hay dữ liệu của ô khác. Bấm lại tên tham số chỉ đưa ô lên trước, không đặt lại mốc. Viền kéo hai bên/dưới rộng 12 px và hai góc dưới có tay kéo 28×28 px màu xanh để co giãn dễ hơn.

**Làm mới tất cả** trên thanh công cụ áp dụng thao tác này cho toàn bộ ô đồ thị đang mở của mọi drone, kể cả các tab chưa được chọn, với cùng mốc thời gian bắt đầu. Bố cục được giữ nguyên. Nếu đang **Dừng hình**, các ô được xóa phần vẽ và sẽ vẽ dữ liệu mới khi bỏ dừng hình.

Ctrl/Cmd hoặc Shift + click nhiều trường, rồi bấm **Vẽ các tham số đã chọn** hoặc double-click lựa chọn: cùng drone và cùng đơn vị sẽ gộp chung; khác đơn vị mở ô riêng. Kéo trường/ô để gộp cũng tuân theo quy tắc này. **Tách đồ thị…** cho chọn đại lượng cần tách, mỗi đại lượng đã chọn mở một ô riêng; phần còn lại giữ chung. **Gộp cùng đơn vị** gộp lại theo từng drone. Nút tách/bỏ đường chỉ hiện khi ô có nhiều đường.

Cuộn chuột hoặc **Zoom +/−** để phóng/thu; kéo chuột để pan. Bật **Khoanh zoom** rồi kéo chọn vùng phóng to. Khi thao tác, ô tự giữ vùng nhìn dù dữ liệu vẫn cập nhật; **Vừa dữ liệu** căn vừa lịch sử hiện có, **Bám lại** trở về dữ liệu mới nhất. Bật lại **Bám thời gian** để tất cả ô tiếp tục bám. **Làm mới** vẫn dùng để bắt đầu lượt vẽ mới, khác với căn vừa dữ liệu.

Hiển thị tối đa 10 Hz, lịch sử 120 giây, hỗ trợ 52 trường telemetry; dữ liệu thiếu/cũ không được vẽ thành số 0. Đồ thị là tiến trình chỉ đọc riêng, đóng nó không dừng điều khiển.

**Hướng dẫn từ setup → mô phỏng → router/PX4 → bay đơn/đội hình → kéo/thả đồ thị:** [docs/quickstart.md](docs/quickstart.md).

## Kết nối PX4 qua router

```text
Leader ↔ Wi-Fi ↔ router ↔ máy tính ↔ router ↔ Wi-Fi ↔ follower
                           │
                 Thuật toán + giám sát + log
```

Cài adapter MAVLink vào môi trường riêng:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[mavlink,plots]'
.venv/bin/python run_cli.py --config config/px4.json --connect udpin:0.0.0.0:14550 --plots
```

Lệnh có `--plots` cần extra `plots` ở trên; bỏ cờ đó nếu chỉ chạy CLI. Module Wi-Fi cần chuyển tiếp MAVLink hai chiều từ PX4 về IP máy tính/UDP 14550; mỗi drone có system ID riêng. Phần mềm tự phát hiện heartbeat PX4 và yêu cầu các stream telemetry.

Trước khi chạy đội hình, khai báo đúng ID, `origin` của mỗi drone và `frame_confirmed` trong [config/px4.json](config/px4.json). Frame chung là NED: North/East/Down, đơn vị mét. Giá trị đặt được chuyển từ frame chung về local NED riêng từng drone.

## Chức năng đã triển khai

| Module trong `src/multi_drone/` | Chức năng |
|---|---|
| `connection.py` | MAVLink 2 qua UDP/TCP, định tuyến lệnh theo system/component ID; kết nối/ngắt/kết nối lại qua CLI. |
| `telemetry.py` | Giải mã 52 trường đã chọn, state/mode/armed/health, kiểm tra timestamp, giá trị thiếu và dữ liệu cũ. |
| `parameters.py` | List/get/set parameter, mã hóa bytewise PX4, kiểm tra phản hồi và đọc lại; chặn ghi khi armed. |
| `commands.py` | Arm/Disarm, Takeoff, Position, Hold, Land, RTL; ACK có tên mã, timeout, kết quả từng drone và thành công một phần. |
| `notifications.py` | Ghép STATUSTEXT nhiều đoạn, lịch sử thông báo có severity/thời gian theo drone; xem bằng `messages` hoặc khung thông báo trong cửa sổ đồ thị. |
| `formation.py` | Chọn leader/follower, offset NED, phát trước setpoint, xác nhận Offboard và giới hạn tốc độ thay đổi mục tiêu. |
| `algorithm_runtime.py` | Thuật toán offset mặc định hoặc nạp `.py`; worker riêng có timeout, kiểm tra đầu ra vị trí/vận tốc. |
| `safety.py` | Kiểm tra độ mới state, armed/in-air, estimator, pin, giới hạn mục tiêu và khoảng cách đội hình. |
| `logger.py` | Session JSONL: telemetry gốc, state chuẩn hóa, lệnh/ACK, setpoint, sai số và sự kiện; replay chỉ đọc. |
| `plotting.py`, `plot_data.py`, `plot_window.py`, `plot_resize.py` | Đồ thị realtime 52 trường theo drone, kéo/thả/gộp/tách/tab, làm mới theo mốc thời gian riêng từng ô, tay kéo co giãn, bộ đệm giới hạn và xử lý dữ liệu cũ. |
| `ui.py` | CLI tương tác, chạy script, watch/status, điều khiển và thử lỗi mô phỏng. |

Khi bắt đầu bám, follower phải đang bay. Phần mềm phát setpoint giữ vị trí trước khoảng 1,2 giây ở 20 Hz mặc định, yêu cầu PX4 vào Offboard và chờ ACK + heartbeat xác nhận. Sau đó `p_target[i] = p_leader + offset[i]`; leader giữ nguyên mode.

Mất state thiết yếu hoặc thuật toán lỗi: dừng bám, yêu cầu Hold/Land/RTL theo cấu hình cho follower còn ở Offboard. Khi máy tính/router mất hoàn toàn, PX4 cần tự xử lý Offboard-loss bằng failsafe onboard.

## Giới hạn log và cache parameter

Log được ghi bằng luồng riêng, hàng đợi tối đa 1.024 bản ghi; khi đầy sẽ bỏ bản ghi thay vì chặn điều khiển. Mỗi phiên giữ tối đa 4 file × 8 MiB, bản ghi tối đa 64 KiB. File `.jsonl` chính chứa dữ liệu mới nhất; `.1.jsonl`, `.2.jsonl`, `.3.jsonl` chứa dữ liệu cũ dần và có thể replay riêng. Giới hạn này áp dụng từng phiên, không tự xóa log của các phiên trước. Khi lỗi ổ đĩa, phần ghi log ngừng nhưng điều khiển/failsafe tiếp tục. Lệnh `log` hoặc `status` hiển thị lỗi và số bản ghi bị bỏ; đóng phiên chờ ghi tối đa 2 giây.

Parameter chỉ được lưu khi có yêu cầu get/set/list tương ứng. Cache giới hạn 4.096 parameter mỗi drone/component và 32 drone/component gần nhất. Danh sách vượt giới hạn hoặc có count/index/tên không nhất quán sẽ báo lỗi; dữ liệu từ lần đọc trước không được tính vào kết quả list mới.

## Thuật toán người dùng

Đặt file trong `algorithms/python/`, khai báo hàm `step(context)` trả về target cho đúng các follower. Ví dụ: [offset_follow.py](algorithms/python/offset_follow.py).

```text
algorithm load algorithms/python/offset_follow.py
formation setup 1 2 3
formation offset 2 -3 -2 0
formation start
formation wait 5
```

Chỉ đổi thuật toán/offset khi đội hình đã dừng. Chạy code `.py` tin cậy; worker xử lý treo/lỗi, không phải sandbox bảo mật.

## Kiểm thử và tài liệu

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python run_cli.py replay logs/SESSION.jsonl --kind formation_state
```

- [Hướng dẫn CLI, cấu hình PX4 và giao diện thuật toán](docs/cli.md).
- [Gửi lệnh, chuyển Position và xem thông báo lỗi PX4](README2.md).
- [Danh sách telemetry và nguồn QGC](docs/telemetry-leader-follower.md).
- Cấu hình mô phỏng: [simulation.json](config/simulation.json); script demo: [demo.txt](examples/demo.txt).

Đã kiểm chứng bằng mô phỏng động học và MAVLink UDP loopback; chưa kiểm chứng với PX4 SITL/HITL hoặc chuyến bay thật. NED hiện hỗ trợ các frame cùng hướng, khác gốc qua phép tịnh tiến; chưa có tự hiệu chuẩn frame, tránh vật cản hay lập đường bay tránh va chạm.

## Telemetry chọn lọc từ QGroundControl

Đối chiếu source **QGC v5.1.4**: chọn **52/95 trường (54,7%) trong 7 nhóm liên quan**, không phải 54,7% toàn bộ QGC. Đây là telemetry/trạng thái, khác parameter cấu hình. Danh sách có cả dữ liệu phục vụ bám, giám sát và chẩn đoán; không phải trường nào cũng bắt buộc với mọi cấu hình.

| Nhóm | Các trường giữ lại |
|---|---|
| `vehicle` (12) | `roll`, `pitch`, `yaw`, `rollRate`, `pitchRate`, `yawRate`, `groundSpeed`, `climbRate`, `altitudeRelative`, `altitudeAMSL`, `throttlePct`, `flightTime` |
| `gps` (6) | `lat`, `lon`, `hdop`, `vdop`, `lock`, `count` |
| `localPosition` (6) | `x`, `y`, `z`, `vx`, `vy`, `vz` |
| `localPositionSetpoint` (6) | `x`, `y`, `z`, `vx`, `vy`, `vz` |
| `estimatorStatus` (14) | `goodAttitudeEsimate`, `goodHorizVelEstimate`, `goodVertVelEstimate`, `goodHorizPosRelEstimate`, `goodHorizPosAbsEstimate`, `goodVertPosAbsEstimate`, `gpsGlitch`, `accelError`, `velRatio`, `horizPosRatio`, `vertPosRatio`, `magRatio`, `horizPosAccuracy`, `vertPosAccuracy` |
| `battery` (8) | `id`, `voltage`, `current`, `percentRemaining`, `mahConsumed`, `temperature`, `timeRemaining`, `chargeState` |

Ngoài 52 trường này, cần ID drone/component, mode, armed, landed state, health, nguồn vị trí hợp nhất, mốc tọa độ, timestamp/độ mới dữ liệu, kết quả lệnh và thông báo lỗi. Follower gửi state thực về máy tính để theo dõi sai số bám.

Ưu tiên state hợp nhất trong **hệ tọa độ chung**; GPS thô và độ cao relative theo home riêng không được dùng thay thế tùy ý. Module Wi-Fi không mặc nhiên có `RADIO_STATUS`; chất lượng link được theo dõi qua tuổi dữ liệu và thống kê gói.

Xem [danh sách chi tiết: đơn vị, message nguồn, mức ưu tiên, cách đếm và source QGC](docs/telemetry-leader-follower.md).
