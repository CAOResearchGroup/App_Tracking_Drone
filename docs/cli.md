# Sử dụng Python Multi-Drone CLI

Lần đầu sử dụng: xem [hướng dẫn từng bước từ setup đến kết nối PX4 và dùng đồ thị](quickstart.md).

## 1. Môi trường và chạy chương trình

Python 3.11 trở lên; bản hiện tại được kiểm thử trên Python 3.14/macOS. Lõi và mô phỏng không có dependency ngoài thư viện chuẩn. Adapter phần cứng dùng pymavlink 2.x; đã kiểm thử với 2.4.50.

Từ thư mục gốc dự án:

```sh
python3 run_cli.py --sim
python3 run_cli.py --sim --config config/simulation.json
python3 run_cli.py --sim --script examples/demo.txt
python3 run_cli.py --sim --execute list --execute 'params get 2 MPC_XY_VEL_MAX'
```

Nếu đã cài package bằng `pip install -e '.[mavlink]'`, có thể dùng lệnh `fleet` thay `python run_cli.py`. Mọi output trạng thái là JSON; dữ liệu thiếu là `null`, không phải số 0 giả. Chế độ script dừng ở lỗi đầu tiên và trả exit code khác 0; CLI tương tác hiển thị lỗi và tiếp tục nhận lệnh. EOF/Ctrl-C ở prompt dừng đội hình và thoát.

Đồ thị tùy chọn: cài `pip install -e '.[mavlink,plots]'` rồi thêm `--plots` vào lệnh chạy. Trong phiên CLI dùng `plot open|close|status`. Cửa sổ Qt ở process riêng, snapshot chỉ đọc tối đa 10 Hz, bộ đệm 120 s; hàng đợi đầy sẽ bỏ frame hiển thị. Kéo trường vào vùng thả để thêm đường, kéo **Kéo để gộp** để hợp nhất ô cùng drone và đơn vị; khác đơn vị mở ô riêng. Tham khảo [thao tác đồ thị](quickstart.md#6-dùng-đồ-thị-thời-gian-thực). Đóng cửa sổ không dừng đội hình; thoát CLI đóng cả hai.

Mỗi `(drone ID, tham số)` chỉ có trong một ô; bấm lại tên tham số chỉ đưa ô đó lên trước. **Làm mới** xóa phần vẽ của ô, đặt mốc thời gian 0 tại lúc bấm và chỉ nhận vào phần vẽ các mẫu từ mốc đó; không ảnh hưởng ô khác/log/kết nối. Nếu đang dừng hình, bỏ dừng hình để xem dữ liệu mới. Tách đường kế thừa mốc; gộp dùng mốc làm mới gần nhất.

Tiêu đề ghi ID và tên trường đầy đủ. Cột **Drone/Tham số** cố định bên trái, chỉ đổi chiều rộng bằng kéo vạch ngăn. Các scope là cửa sổ con trong vùng bên phải; viền kéo hai bên/dưới rộng 12 px và tay kéo góc dưới 28×28 px giúp co giãn dễ hơn. **Xếp tab/Xếp dọc** chuyển chế độ và sắp xếp các ô. **Tách đồ thị…** cho chọn đường cần mở ô riêng, giữ phần còn lại chung ô. Kéo/gộp chuyển quyền hiển thị trường sang ô đích, không nhân bản đường. Zoom bằng cuộn chuột, nút **Zoom +/−** hoặc **Khoanh zoom**; **Bám lại** trở về dữ liệu mới nhất. Cập nhật riêng code đồ thị bằng `plot close` → `plot open`. Riêng bản đổi `vehicle.heading` thành `vehicle.yaw` cần chạy lại CLI để nạp cả bộ giải mã mới.

## 2. Kết nối PX4 qua Wi-Fi/router

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[mavlink]'
.venv/bin/python run_cli.py --config config/px4.json --connect udpin:0.0.0.0:14550
```

Máy tính và module Wi-Fi của các drone cùng mạng router. Bridge Wi-Fi trên từng drone cần đọc MAVLink từ PX4 và gửi UDP đến IP máy tính, port 14550, đồng thời chuyển gói chiều ngược lại về PX4. Router không tự chuyển đổi giao thức. Mỗi drone có system ID riêng; GCS mặc định ID 250, component 190. Chương trình chỉ đăng ký heartbeat từ autopilot PX4.

Một UDP input có thể nhận nhiều drone. Pymavlink học các địa chỉ peer từ gói đến; gói gửi có target system/component cụ thể để autopilot xử lý đúng. Khi chưa có heartbeat, chưa có peer để gửi lệnh. Tránh để ứng dụng khác chiếm cùng port.

Endpoint hỗ trợ: `udpin:host:port`, `udpout:host:port`, `tcp:host:port`, `tcpin:host:port`. Với TCP/UDP output tới một bridge, bridge đó phải định tuyến được các drone cần điều khiển. Phiên bản này quản lý một endpoint trong một phiên CLI.

```text
list
status 1
streams all
watch 10
disconnect
connect udpin:0.0.0.0:14550
```

Chương trình tự yêu cầu message interval khi lần đầu phát hiện một drone. Stream bị firmware từ chối được ghi ACK trong log; dữ liệu chưa được phát vẫn là unavailable. Không tự ghi tham số PX4 hoặc vô hiệu hóa arming check/failsafe để ép kết nối.

## 3. Cấu hình frame và đội hình

Sửa `config/px4.json` theo phần cứng. Không dùng origin của mô phỏng cho máy bay thật.

- `drones[].system_id`: đúng với system ID của PX4.
- `drones[].origin`: tọa độ gốc local của drone trong hệ NED chung, mét. Công thức `p_common = p_local + origin`.
- `drones[].offset`: vị trí follower mong muốn so với leader trong hệ NED chung.
- `frame_confirmed`: chỉ đặt true sau khi xác định các frame cùng hướng và phép đổi gốc đúng.
- `rate_hz`: 5–50 Hz; mặc định 20 Hz.
- `heartbeat_timeout`, `state_timeout`, `health_timeout`: ngưỡng hết hạn theo giây.
- `min_separation`: khoảng cách 3D nhỏ nhất giữa các drone/target.
- `max_speed`: tốc độ vector cho velocity target, hoặc tốc độ thay đổi position target.
- `min_altitude`, `max_altitude`: giới hạn `-z` trong hệ chung, không phải AMSL.
- `max_radius`: bán kính ngang tính từ gốc chung.
- `max_tracking_error`: khoảng cách tối đa từ target position đến vị trí thực.
- `min_battery`, `battery_id`: phần trăm pin tối thiểu và pin được dùng để kiểm tra.
- `algorithm_timeout`: deadline mỗi lần tính thuật toán; nhỏ hơn một chu kỳ.
- `failsafe`: `hold`, `land` hoặc `rtl`, chỉ áp dụng cho follower do phiên điều phối quản lý.

NED: X=North, Y=East, Z=Down; bay lên tương ứng Z âm. Bản hiện tại chỉ tịnh tiến gốc giữa các frame NED cùng hướng. Không suy ra origin từ home một cách tự động; trường hợp cần đổi góc giữa frame phải bổ sung transform trước khi dùng. Tọa độ local riêng của leader không được gửi nguyên cho follower nếu origin khác nhau.

## 4. Lệnh điều khiển và parameter

```text
params list 2 MPC
params get 2 MPC_XY_VEL_MAX
params set 2 MPC_XY_VEL_MAX 4
arm 2,3
takeoff followers 3
hold followers
position 2,3
land 2
rtl 3
disarm 2
```

`takeoff TARGET HEIGHT` dùng độ cao so với home, đổi thành AMSL cho `MAV_CMD_NAV_TAKEOFF`; cần có home từ autopilot. ACK accepted chỉ xác nhận autopilot chấp nhận lệnh, không xác nhận đã bay xong. Theo dõi `landed_state`, mode và vị trí thực qua `watch/status`.

Disarm cần trạng thái ON_GROUND mới. Ghi parameter chỉ khi drone disarmed/on-ground; phần mềm đọc type trước, mã hóa bytewise PX4, so giá trị phản hồi và đọc lại để xác minh. Firmware vẫn chịu trách nhiệm kiểm tra range/điều kiện của parameter. Không hỗ trợ parameter string/64-bit hoặc mọi extension protocol; danh sách thiếu gói sẽ timeout, không báo đồng bộ hoàn tất giả.

Lệnh nhóm trả `accepted`, `partial` hoặc `failed` kèm kết quả riêng từng drone. Command timeout có nghĩa kết quả chưa biết; không tự gửi lặp lệnh bay. Các command cùng ID trên cùng drone được tuần tự hóa bằng cách từ chối yêu cầu đồng thời.

`position TARGET` yêu cầu PX4 POSCTL (custom main mode 3, submode 0). Thành công cần ACK và heartbeat mới xác nhận POSCTL; chuyển mode không tự arm/cất cánh. PX4 vẫn kiểm tra điều kiện; app không ép mode hoặc vô hiệu hóa pre-arm checks.

`messages [TARGET] [limit]` xem lịch sử thông báo (limit 1..100, mặc định 50); `messages watch TARGET [seconds]` theo dõi mới (mặc định 30 giây). Kết quả lệnh bị từ chối có `result_name`, `error`, `recent_messages` từ đúng drone trong khoảng gửi lệnh; không khẳng định mọi thông báo gần đó đều là nguyên nhân. Cửa sổ `--plots` có khung thông báo với bộ lọc drone/severity. Nguồn `STATUSTEXT` là nội dung từ PX4; `APP_COMMAND` là lỗi xử lý lệnh của app. Lịch sử tối đa 100 dòng/drone, không biểu thị lỗi còn hiệu lực. Chưa hỗ trợ Events/Arming Check Report theo metadata firmware như QGC; xem chi tiết trong [README2](../README2.md).

## 5. Vận hành leader–follower

Leader và follower được operator đưa vào trạng thái armed/in-air trước. Bộ điều phối không tự arm/cất cánh khi `formation start`.

```text
formation setup 1 2 3
formation offset 2 -3 -2 0
formation offset 3 -3 2 0
formation start
formation wait 5
formation status
formation stop
```

Vòng đời: `idle → priming → switching → active`. Khi priming, máy tính giữ target tại vị trí hiện tại của follower và phát stream trước khoảng 1,2 giây. Sau đó gửi yêu cầu Offboard, duy trì stream và chờ cả ACK lẫn heartbeat xác nhận mode. Chỉ khi active mới áp target đội hình; giới hạn tốc độ dịch chuyển target để không nhảy ngay đến offset mới. Leader không bị chuyển mode.

Mode Offboard yêu cầu stream liên tục; xem [PX4 Offboard](https://docs.px4.io/main/en/flight_modes/offboard.html). Cần kiểm tra `COM_OF_LOSS_T` và `COM_OBL_RC_ACT` phù hợp trên PX4. Ứng dụng không tự sửa chúng. Khi máy tính/router chết, ứng dụng không còn khả năng gửi Hold; failsafe onboard phải xử lý.

Mất/stale state, estimator xấu, pin thấp, target vi phạm giới hạn hoặc thuật toán lỗi sẽ dừng bám. Phần mềm yêu cầu hành vi `failsafe` với các follower còn ở Offboard và còn link. Follower đã được pilot chuyển sang mode khác không bị ép trở lại. Lệnh thủ công cho follower sẽ dừng đội hình để tránh hai nguồn phát mục tiêu xung đột.

Sai số trong `formation status` là target đang phát trừ vị trí follower thực, trong hệ NED chung. Giá trị target autopilot báo lại chỉ là phản hồi chẩn đoán, không phải ACK hoàn tất setpoint. Bộ kiểm tra khoảng cách phát hiện vi phạm, chưa phải thuật toán tránh va chạm/vật cản hoặc bảo đảm đường đi không giao nhau.

## 6. Viết thuật toán Python

```text
algorithm load algorithms/python/offset_follow.py
algorithm status
algorithm builtin
```

Đổi/nạp thuật toán khi idle. File phải định nghĩa `step(context)` và có thể giữ biến nội bộ giữa các lần gọi. Khi reload, worker và state nội bộ được tạo lại.

Input là dict:

```python
{
    "dt": 0.05, "elapsed": 2.0,
    "leader": {"id": 1, "position": [0, 0, -3], "velocity": [1, 0, 0],
               "yaw": 0.0, "state_age": 0.02},
    "followers": [
        {"id": 2, "position": [-3, -2, -3], "velocity": [1, 0, 0],
         "yaw": 0.0, "state_age": 0.02, "offset": [-3, -2, 0]}
    ]
}
```

Output: đúng một target cho từng follower, chọn **position hoặc velocity**, optional yaw (radian). Mọi position/velocity trong hệ NED chung, không phải body frame.

```python
def step(context):
    p = context["leader"]["position"]
    return {
        str(f["id"]): {"position": [p[i] + f["offset"][i] for i in range(3)]}
        for f in context["followers"]
    }
```

Ví dụ target vận tốc: `{"2": {"velocity": [0.2, 0.0, 0.0], "yaw": 0.0}}`. Velocity target cần health estimate vận tốc hợp lệ. Target không hợp lệ, thiếu/thừa follower, NaN/Inf, exception hoặc quá deadline đều dừng bám. Plugin chạy trong process riêng để có thể kết thúc khi treo; chỉ chạy code tin cậy vì đây không phải sandbox giới hạn file/mạng. Không có MATLAB Engine và chưa có bộ chuyển `.m`.

## 7. Mô phỏng lỗi và log

```text
sim move 1 3 0 -3
sim freeze 1 on
sim freeze 1 off
sim drop 1 on
sim drop 1 off
sim reject 3 on
sim reject 3 off
log
```

`freeze` giữ heartbeat nhưng ngừng pose để kiểm tra stale state. `drop` mô phỏng mất cả chiều gửi/nhận của drone đó. Simulator có watchdog Offboard riêng khi stream dừng. Đây là mô hình động học test phần mềm, không mô phỏng khí động học, radio thực hoặc toàn bộ firmware PX4.

Mỗi phiên tạo file riêng trong `logs/`, không ghi đè. Log chứa telemetry gốc, snapshot state 1 Hz, lệnh/ACK, parameter, target và sự kiện đội hình. Replay không tạo connection:

```sh
python3 run_cli.py replay logs/SESSION.jsonl --kind vehicle_state
python3 run_cli.py replay logs/SESSION.jsonl --kind formation_state --kind formation_stopped
python3 run_cli.py replay logs/SESSION.jsonl --speed 2
```

## 8. Kiểm chứng và giới hạn

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Test bao gồm UDP MAVLink 2 loopback nhiều system ID, telemetry/sentinel/đơn vị, timestamp cũ/reboot, parameter bytewise/readback, ACK/timeout, partial failure, worker timeout, bám leader, mất leader/stale pose và pilot takeover. Test đồ thị kiểm tra bộ đệm, dữ liệu cũ, reconnect, sự kiện drag/drop Qt, gộp/tách theo drone/đơn vị và vòng đời process. Test UDP/Qt được skip nếu chưa cài extra tương ứng; cài `.[mavlink,plots]` để chạy đủ. Test Qt dùng nền offscreen.

Chưa kiểm chứng trên PX4 SITL/HITL hoặc drone thật. Adapter hiện tập trung PX4 multicopter, local NED cùng hướng và một link; chưa có MAVLink signing, tự hiệu chuẩn frame, GUI gửi lệnh bay, mission planner hoặc path planner tránh va chạm. Cửa sổ đồ thị chỉ giám sát telemetry, không gửi lệnh. Prototype CLI không thay thế vòng điều khiển thời gian thực/failsafe của PX4.
