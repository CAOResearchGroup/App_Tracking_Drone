# Gửi lệnh điều khiển đến drone bằng CLI

Tài liệu tiếp nối [README1.md](README1.md): sau khi nhận được telemetry, dùng CLI để gửi lệnh đến drone PX4 qua cùng kết nối MAVLink hai chiều.

```text
Lệnh tại fleet> → máy tính → router → module Wi-Fi → PX4
              ← ACK và telemetry từ PX4 quay về ←
```

Các ví dụ dùng drone ID `1`. Lệnh trong khối `sh` chạy ở Terminal; lệnh trong khối `text` nhập tại dấu nhắc `fleet>`, không gõ thêm chữ `fleet>`.

## 1. Chọn mô phỏng hoặc drone thật

### Thử lệnh bằng mô phỏng

Từ thư mục dự án, chạy:

```sh
python3 run_cli.py --sim
```

Mô phỏng tích hợp có ba drone ID `1`, `2`, `3`, không cần phần cứng. Có thể thực hành trình tự ở mục 4 trước. Demo đội hình có sẵn:

```sh
python3 run_cli.py --sim --script examples/demo.txt
```

File demo có `require-sim` để chặn chạy nhầm trên kết nối thật. Mô phỏng này dùng mô hình động học, không thay thế kiểm chứng với PX4 thật.

### Kết nối drone thật

Hoàn thành setup trong [README1.md](README1.md), sau đó chạy:

```sh
cd /Users/vungocson0503/Desktop/Python_Multi_Drone
.venv/bin/python run_cli.py --config config/px4.json --connect udpin:0.0.0.0:14550 --plots
```

Bỏ `--plots` nếu chỉ dùng CLI. Không thêm `--sim` khi kết nối thật. Module Wi-Fi phải chuyển MAVLink cả hai chiều: nhìn thấy telemetry chưa chứng minh đường gửi lệnh ngược về PX4 đã hoạt động.

Khi thử trên bàn, tháo cánh quạt; không thực hiện quy trình cất cánh ở mục 4 trên bàn. Chỉ thực hiện lệnh bay thật trong điều kiện thử bay phù hợp và có người điều khiển sẵn sàng tiếp quản. Theo tài liệu dự án, phần mềm chưa được kiểm chứng trên PX4 SITL/HITL hoặc chuyến bay thật.

Ứng dụng tự gửi heartbeat/yêu cầu telemetry khi kết nối, nhưng không tự arm hoặc cất cánh. `origin: null` và `frame_confirmed: false` chặn chạy đội hình; chúng **không khóa** các lệnh arm/takeoff/land thông thường.

## 2. Kiểm tra đúng drone và trạng thái

```text
list
status 1
config
```

- `list`: xem ID, mode, armed, `landed_state`, vị trí, pin và tuổi dữ liệu.
- `status 1`: xem chi tiết drone 1; gồm `last_status` nếu PX4 có gửi thông báo.
- `config`: xem ID khai báo, vai trò, giới hạn và hành vi failsafe hiện tại.

Trong `list`, `landed_state: 1` là ON_GROUND, `2` là IN_AIR. Trong `status 1`, trường tương ứng tên là `landed`. Chờ dữ liệu cập nhật trước khi gửi lệnh; trường thiếu có thể là `null`.

Nếu thiếu luồng telemetry:

```text
streams 1
watch 5
```

`watch 5` chỉ hiển thị trong 5 giây, không chờ một hành động bay hoàn tất.

## 3. Cú pháp và chọn drone nhận lệnh

| Lệnh mẫu | Yêu cầu gửi đến PX4 |
|---|---|
| `arm 1` | Arm drone 1; PX4 vẫn có thể từ chối theo điều kiện của autopilot. |
| `disarm 1` | Disarm drone 1; ứng dụng chỉ cho gửi khi ON_GROUND còn mới. |
| `takeoff 1 3` | Yêu cầu cất cánh đến độ cao 3 m so với home. |
| `position 1` | Yêu cầu chuyển sang Position (`POSCTL`); chờ ACK và heartbeat mới xác nhận mode. |
| `hold 1` | Yêu cầu chuyển sang Hold. |
| `land 1` | Yêu cầu chuyển sang Land. |
| `rtl 1` | Yêu cầu chuyển sang Return/RTL; đường về và hành vi do PX4 quyết định. |

`takeoff` không ghi độ cao thì dùng **3 m**. Ứng dụng yêu cầu drone đã armed, báo ON_GROUND và có độ cao home AMSL để mã hóa lệnh. Độ cao phải nằm trong `min_altitude`–`max_altitude`; file `config/px4.json` hiện đặt **0,5–30 m**. Các kiểm tra này không thay thế kiểm tra điều kiện bay của PX4.

Vị trí nhận `TARGET` hỗ trợ:

| TARGET | Phạm vi |
|---|---|
| `1` | Một drone ID 1. |
| `2,3` | Hai drone ID 2 và 3; không chèn khoảng trắng giữa các ID. |
| `all` | Tất cả ID khai báo trong config, không phải mọi drone tự phát hiện. |
| `leader` | Leader hiện được chọn trong bộ điều phối. |
| `followers` | Các follower hiện được chọn trong bộ điều phối. |

Ví dụ `hold 2,3` hoặc `land followers`. Với một drone thật, dùng ID cụ thể: config mẫu khai báo cả `1`, `2`, `3`, nên `all` còn gửi yêu cầu xử lý cho các ID chưa kết nối.

### Chuyển sang Position

Ví dụ với drone 3 và 4 đang kết nối:

```text
position 3,4
status 3
status 4
```

Đọc `mode: "POSCTL"` và `state_confirmed: true` trong kết quả lệnh. App gửi yêu cầu chuyển mode, không tự arm hoặc cất cánh và không bỏ qua kiểm tra của PX4. Nếu ACK được chấp nhận nhưng không có heartbeat mới xác nhận POSCTL trong 3 giây, kết quả vẫn báo thất bại/không xác nhận được. Lệnh `position` cho follower đang bám sẽ dừng đội hình trước.

Position là mode điều khiển bay của PX4; lệnh này không cung cấp joystick từ bàn phím. `takeoff` là yêu cầu bay tự động riêng và có thể làm mode thay đổi; luôn xem lại trạng thái thực sau mỗi lệnh.

## 4. Trình tự điều khiển một drone

Thực hành bằng `--sim` trước. Với drone thật, nhập từng bước và kiểm tra trạng thái giữa các bước, không dán toàn bộ quy trình thành một lượt.

### Bước A — Arm

```text
arm 1
status 1
```

Kiểm tra kết quả lệnh và `armed: true`. Arm chưa phải lệnh cất cánh. Nếu bị từ chối, xem `last_status` và xử lý nguyên nhân trước khi tiếp tục.

### Bước B — Cất cánh

```text
takeoff 1 3
watch 5
status 1
```

Theo dõi drone chuyển sang IN_AIR và độ cao thực. `watch 5` không bảo đảm đã lên đủ 3 m. `vehicle.altitudeRelative`, nếu có, là dữ liệu độ cao relative được giải mã; không mặc định coi `-localPosition.z` là độ cao so với home vì local origin có thể khác home.

### Bước C — Yêu cầu Hold khi đang bay

```text
hold 1
status 1
```

Xem mode và trạng thái thực để xác nhận phản ứng. Lệnh này không phải nút ngắt động cơ.

### Bước D — Hạ cánh

```text
land 1
watch 5
status 1
```

Tiếp tục theo dõi cho đến khi drone đã đáp và báo ON_GROUND. Không dùng thời gian chờ cố định làm bằng chứng đã hạ cánh.

### Bước E — Disarm nếu vẫn còn armed, rồi thoát

Sau khi xác nhận ON_GROUND:

```text
disarm 1
status 1
```

Nếu PX4 đã tự disarm thì không cần gửi lại. Khi đã xác nhận `armed: false`, có thể thoát:

```text
quit
```

`quit` và `disconnect` **không phải lệnh hạ cánh hoặc disarm**. Nếu đang chạy đội hình, chúng dừng bộ điều phối và yêu cầu failsafe với follower đủ điều kiện; với cấu hình mặc định, yêu cầu đó là Hold.

## 5. Đọc kết quả lệnh và xử lý timeout

CLI trả JSON. Ví dụ kết quả arm thành công, rút gọn:

```json
{
  "status": "accepted",
  "results": [
    {
      "target": 1,
      "action": "arm",
      "result": 0,
      "acknowledged": true,
      "state_confirmed": true
    }
  ]
}
```

- `accepted`: tất cả drone trong yêu cầu đáp ứng tiêu chí thành công của ứng dụng.
- `partial`: một số drone thành công, một số lỗi; đọc từng mục trong `results`.
- `failed`: không drone nào đáp ứng tiêu chí thành công.
- `acknowledged: true`: nhận ACK chấp nhận lệnh. **Không có nghĩa takeoff/land/RTL đã thực hiện xong.**
- `state_confirmed`: bổ sung cho arm/disarm và position được chấp nhận; ứng dụng chờ heartbeat xác nhận trạng thái armed hoặc mode POSCTL tương ứng.
- `result_name`: tên mã ACK dễ đọc, ví dụ `TEMPORARILY_REJECTED`, `DENIED`, `UNSUPPORTED`; trường `error` giải thích kết quả từ chối.
- `recent_messages`: thông báo STATUSTEXT của đúng drone nhận được từ lúc gửi lệnh đến lúc trả kết quả lỗi. Đây là thông báo cùng khoảng thời gian, không bảo đảm tất cả đều là nguyên nhân của lệnh đó. App chờ thêm 0,2 giây khi có lỗi để nhận thông báo theo sau ACK; thông báo đến muộn hơn vẫn xem được trong lịch sử.
- `correlation_id`: xuất hiện trong kết quả ACK và log để đối chiếu lệnh.

Nếu không nhận ACK cuối, thông báo có dạng `No final ACK ...; result unknown`. Lệnh có thể đã đến drone nhưng ACK bị mất. Kiểm tra telemetry/trạng thái thực và kết nối trước khi quyết định gửi lại; ứng dụng không tự gửi lặp lệnh bay. Lệnh nhóm không tự hoàn tác những drone đã nhận lệnh thành công.

## 6. Đọc và ghi parameter

Xem danh sách hoặc đọc giá trị:

```text
params list 1 MPC
params get 1 MPC_XY_VEL_MAX
```

`MPC` là bộ lọc tên sau khi lấy danh sách. Ví dụ ghi dưới đây dùng để thực hành trong mô phỏng, không phải giá trị khuyến nghị cho mọi drone thật:

```text
require-sim
params set 1 MPC_XY_VEL_MAX 4
params get 1 MPC_XY_VEL_MAX
```

`require-sim` kiểm tra phiên hiện tại. Trong script, lỗi làm dừng script; ở CLI tương tác, lỗi chỉ kết thúc lệnh hiện tại, không khóa các lệnh nhập sau.

Khi ghi parameter trên drone thật, chọn đúng tên và giá trị phù hợp cấu hình của bạn rồi dùng cú pháp `params set ID NAME VALUE`. Ứng dụng yêu cầu disarmed, ON_GROUND còn mới, đọc kiểu dữ liệu trước, gửi giá trị và đọc lại xác minh. Đọc lại thành công chưa chứng minh thay đổi đã có hiệu lực sau reboot hoặc không cần reboot; điều đó phụ thuộc parameter/firmware.

## 7. Điều khiển đội hình leader–follower

Phần này chỉ dùng khi đã xác nhận hệ tọa độ chung và điều kiện bay của toàn đội. Trong config phải khai báo đúng ID, origin từng drone và `frame_confirmed: true` sau khi kiểm chứng frame thực tế. Không sao chép origin mô phỏng sang drone thật để bỏ qua kiểm tra.

Leader và follower đều phải đang armed/IN_AIR, có vị trí và health đủ mới, estimator phù hợp, pin và khoảng cách đáp ứng giới hạn. `formation start` không tự arm/cất cánh.

Khi đội hình đang dừng, chọn leader 1, follower 2 và 3:

```text
formation setup 1 2 3
formation offset 2 -3 -2 0
formation offset 3 -3 2 0
formation start
formation wait 5
formation status
```

Offset dùng mét trong hệ NED chung: North/East/Down. Ví dụ `-3 -2 0` là 3 m về Nam, 2 m về Tây, cùng tọa độ Down với leader; không phải khoảng cách theo hướng đầu drone.

Ứng dụng phát trước setpoint khoảng 1,2 giây, yêu cầu follower vào Offboard, rồi chờ ACK và heartbeat xác nhận. Chỉ `phase: active` mới bắt đầu bám; leader giữ mode hiện tại. Cấu hình mẫu gửi setpoint ở 20 Hz.

Dừng bám và yêu cầu hạ follower:

```text
formation stop
land followers
```

Sau đó theo dõi từng drone đến ON_GROUND trước khi disarm. Leader không nằm trong `followers`; điều khiển leader riêng khi cần.

`formation stop` dùng hành vi `failsafe` trong config (`hold` mặc định, hoặc `land`/`rtl`) cho follower còn Offboard và heartbeat đủ mới. Lệnh thủ công nhắm đến follower cũng dừng đội hình trước khi xử lý lệnh. Khi máy tính/router mất hoàn toàn, ứng dụng không thể gửi lệnh dự phòng; cần kiểm chứng failsafe onboard của PX4. Xem thêm [hướng dẫn đội hình](docs/cli.md).

## 8. Tra lỗi nhanh và xem log

### Xem thông báo PX4 và lỗi lệnh

```text
messages
messages 3
messages 3,4 20
messages watch 3 30
```

`messages` xem lịch sử của mọi drone đã phát hiện; có TARGET thì lọc theo ID/nhóm như các lệnh điều khiển. Số cuối giới hạn kết quả từ 1 đến 100, mặc định 50. `messages watch 3 30` theo dõi thông báo mới của drone 3 trong 30 giây; Ctrl-C chỉ dừng xem. `status 3` cũng chứa `status_messages`, `last_status`, `last_status_at` và `last_status_severity`.

Khi mở `--plots`, khung **Thông báo PX4 / lỗi lệnh — lịch sử nhận** ở dưới đồ thị tự cập nhật, kể cả khi bật Dừng hình. Mỗi dòng có giờ nhận, drone, mức nghiêm trọng, nguồn và nội dung. Có bộ lọc drone, bộ lọc lỗi/cảnh báo; rê chuột lên nội dung để đọc toàn bộ dòng dài. Nếu đã đóng khung, bật lại bằng nút tương ứng trên thanh công cụ.

- `STATUSTEXT`: nội dung do PX4 gửi; các đoạn MAVLink 2 được ghép theo drone/component/message ID. Thiếu đoạn sẽ được đánh dấu, không hiển thị đuôi thông báo như một câu đầy đủ.
- `APP_COMMAND`: lỗi gửi/xác nhận lệnh của ứng dụng, bao gồm tên mã ACK; không phải nguyên văn thông báo từ PX4.
- Giữ tối đa 100 thông báo mỗi drone, khung đồ thị hiển thị tối đa 300 dòng mới nhất sau lọc. Kết nối lại tạo lịch sử mới. Đây là **lịch sử**, không phải danh sách lỗi vẫn còn hiệu lực.

**Giới hạn so với QGC:** phiên bản này hiển thị STATUSTEXT và kết quả lệnh, chưa giải mã giao thức Events/Health and Arming Check Report của PX4. Firmware chỉ gửi nguyên nhân qua Events có thể không cung cấp câu lỗi trong app; khi đó xem Arming Check Report trong QGC. Mã ACK từ chối không tự cho biết cảm biến hay điều kiện cụ thể nào sai. Cơ chế Events cần metadata tương ứng firmware; xem [MAVLink Events Interface](https://mavlink.io/en/services/events.html).

### Một số thông báo thường gặp

| Hiện tượng/thông báo | Kiểm tra tiếp |
|---|---|
| `Drone missing or heartbeat expired` | ID trong `list`, tuổi heartbeat, đường kết nối. |
| Có telemetry nhưng lệnh timeout | Chiều máy tính → module → PX4, ACK quay về và trạng thái thực của drone. |
| `Takeoff requires armed, ON_GROUND drone` | `armed` và `landed` trong `status ID`. |
| `Home AMSL is unavailable` | Dữ liệu home từ PX4; có thể yêu cầu lại `streams ID`. |
| `Disarm requires fresh ON_GROUND state` | Drone đã đáp hay chưa, tuổi dữ liệu landed state. |
| `Parameter writes require disarmed, ON_GROUND drone` | Đáp, disarm và chờ telemetry xác nhận trước khi ghi. |
| `partial` khi dùng `all` | Từng mục `results` và danh sách ID khai báo trong config. |
| `OFFBOARD not confirmed ...` hoặc đội hình trở về `idle` | `formation status`, trường `reason`, health và cấu hình frame. |

Tại CLI:

```text
log
status
help
```

`log` cho đường dẫn log và tình trạng ghi log. Sau phiên, thay `SESSION.jsonl` bằng tên file thực để xem lệnh và ACK:

```sh
python3 run_cli.py replay logs/SESSION.jsonl --kind command_sent --kind command_ack --kind command_timeout
```

Replay chỉ đọc file, không kết nối hay gửi lại lệnh. Nếu log đã xoay vòng, kiểm tra cả các file `.1.jsonl`, `.2.jsonl`, `.3.jsonl` tương ứng. Log có thể thiếu sự kiện khi hàng đợi đầy hoặc lỗi ổ đĩa; xem `logging.error` và `logging.dropped` trong `status`.

Các mô tả lệnh và điều kiện ở đây dựa trên [ui.py](src/multi_drone/ui.py), [app.py](src/multi_drone/app.py), [commands.py](src/multi_drone/commands.py) và [formation.py](src/multi_drone/formation.py) của dự án.
