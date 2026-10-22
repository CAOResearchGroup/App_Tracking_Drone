# Telemetry cho điều khiển tập trung leader–follower

Bộ dữ liệu đề xuất cho luồng **leader → Wi-Fi/router → máy tính → Wi-Fi/router → follower**. Áp dụng cho cả leader và follower; máy tính cần phản hồi follower để theo dõi sai số bám.

## 1. Nguồn và cách đếm

Đối chiếu source **QGroundControl v5.1.4**, thay vì nhánh master thay đổi liên tục. QGC nhận và giải mã dữ liệu do autopilot gửi; QGC không quyết định mọi drone đều phát được các trường này.

“Tham số” trong tài liệu này là **trường telemetry/trạng thái**, khác parameter cấu hình đọc/ghi bằng `PARAM_*`. Chưa chốt firmware nên chưa thể lập danh sách parameter cấu hình dùng chung cho mọi bộ điều khiển. [MAVLink Parameter Protocol](https://mavlink.io/en/services/parameter.html).

Kiểm kê [CMakeLists.txt của FactGroups](https://github.com/mavlink/qgroundcontrol/blob/v5.1.4/src/Vehicle/FactGroups/CMakeLists.txt): **19 file JSON, 182 định nghĩa metadata Fact**. Đây không phải tổng mọi trường QGC nhận được: còn property trong Vehicle, Fact đăng ký bằng code và nhóm lặp cho GPS/pin/ESC. `LocalPositionFact.json` được dùng lại cho nhóm local setpoint qua resource alias.

Để chọn dữ liệu phục vụ bài toán hiện tại, phạm vi so sánh là **7 nhóm, 95 trường**, mỗi nhóm tính một lần; chọn **52/95 = 54,7%**. Tỷ lệ này **không phải 54,7% toàn bộ QGC**. Không đưa camera, động cơ đốt trong, máy phát, tàu ngầm hoặc mọi parameter firmware vào mẫu đếm.

| Nhóm QGC | Schema nguồn | Số trường trong mẫu | Giữ lại |
|---|---|---:|---:|
| `vehicle` | [VehicleFact.json](https://github.com/mavlink/qgroundcontrol/blob/v5.1.4/src/Vehicle/FactGroups/VehicleFact.json) | 27 | 12 |
| `gps` | [GPSFact.json](https://github.com/mavlink/qgroundcontrol/blob/v5.1.4/src/Vehicle/FactGroups/GPSFact.json) | 17 | 6 |
| `localPosition` | [LocalPositionFact.json](https://github.com/mavlink/qgroundcontrol/blob/v5.1.4/src/Vehicle/FactGroups/LocalPositionFact.json) | 6 | 6 |
| `localPositionSetpoint` | [LocalPositionFact.json](https://github.com/mavlink/qgroundcontrol/blob/v5.1.4/src/Vehicle/FactGroups/LocalPositionFact.json) | 6 | 6 |
| `estimatorStatus` | [EstimatorStatusFactGroup.json](https://github.com/mavlink/qgroundcontrol/blob/v5.1.4/src/Vehicle/FactGroups/EstimatorStatusFactGroup.json) | 20 | 14 |
| `battery` | [BatteryFact.json](https://github.com/mavlink/qgroundcontrol/blob/v5.1.4/src/Vehicle/FactGroups/BatteryFact.json) | 12 | 8 |
| `radioStatus` | [RadioStatusFact.json](https://github.com/mavlink/qgroundcontrol/blob/v5.1.4/src/Vehicle/FactGroups/RadioStatusFact.json) | 7 | 0 |
| **Tổng** | | **95** | **52** |

## 2. Danh sách 52 trường được chọn

Phân loại theo vai trò, không coi cả 52 trường đều là điều kiện bắt buộc để được bay:

- **C:** dữ liệu phục vụ tính toán mục tiêu/bám đội hình.
- **S:** giám sát trạng thái và điều kiện vận hành.
- **D:** chẩn đoán, đối chiếu và ghi log; thiếu trường này không tự động chặn bay.
- **Dấu \*:** phụ thuộc kiểu thuật toán, nguồn định vị hoặc chế độ điều khiển. Ví dụ offset cố định không bắt buộc hướng leader; chỉ kiểm tra cờ local/global tương ứng với cách điều khiển đang dùng.

Tên trường giữ theo metadata QGC; đơn vị bên dưới là sau khi giải mã/chuẩn hóa. Mỗi trục và mỗi cờ là một trường riêng trong phép đếm.

### Trạng thái bay — 12 trường

Mapping được đối chiếu trong [VehicleFactGroup.cc](https://github.com/mavlink/qgroundcontrol/blob/v5.1.4/src/Vehicle/FactGroups/VehicleFactGroup.cc).

| # | Trường QGC | Đơn vị | Vai trò | Nguồn / xử lý | Mục đích |
|---:|---|---|---|---|---|
| 1 | `vehicle.roll` | deg | S | `ATTITUDE.roll hoặc ATTITUDE_QUATERNION.q` | Giám sát độ nghiêng quanh trục dọc. |
| 2 | `vehicle.pitch` | deg | S | `ATTITUDE.pitch hoặc ATTITUDE_QUATERNION.q` | Giám sát độ nghiêng quanh trục ngang. |
| 3 | `vehicle.yaw` | deg | C* | `ATTITUDE.yaw hoặc ATTITUDE_QUATERNION.q` | Tên trong ứng dụng cho trường heading của QGC; hiển thị 0–360°. Hướng leader; cần nếu đội hình/giá trị đặt phụ thuộc hướng. |
| 4 | `vehicle.rollRate` | deg/s | D | `ATTITUDE_QUATERNION.rollspeed` | Phân tích đáp ứng quay của drone. |
| 5 | `vehicle.pitchRate` | deg/s | D | `ATTITUDE_QUATERNION.pitchspeed` | Phân tích đáp ứng quay của drone. |
| 6 | `vehicle.yawRate` | deg/s | D | `ATTITUDE_QUATERNION.yawspeed` | Theo dõi thay đổi hướng và đáp ứng khi rẽ. |
| 7 | `vehicle.groundSpeed` | m/s | S | `VFR_HUD.groundspeed` | Giám sát tốc độ bay; không thay thế vector vận tốc. |
| 8 | `vehicle.climbRate` | m/s | S | `VFR_HUD.climb` | Giám sát tốc độ lên/xuống. |
| 9 | `vehicle.altitudeRelative` | m | S | `ALTITUDE.altitude_relative; fallback GLOBAL_POSITION_INT.relative_alt` | Độ cao theo mốc riêng của drone; không so trực tiếp giữa các drone. |
| 10 | `vehicle.altitudeAMSL` | m | C* | `ALTITUDE.altitude_amsl; fallback GLOBAL_POSITION_INT.alt` | Độ cao AMSL khi chuyển vị trí toàn cục về hệ chung. |
| 11 | `vehicle.throttlePct` | % | D | `VFR_HUD.throttle` | Theo dõi tải điều khiển, dấu hiệu gần giới hạn lực đẩy. |
| 12 | `vehicle.flightTime` | s | D | `QGC tính từ thời gian armed, xem Vehicle.cc` | Đánh dấu thời gian phiên bay để đối chiếu log. |

### GPS — 6 trường

Mapping được đối chiếu trong [VehicleGPSFactGroup.cc](https://github.com/mavlink/qgroundcontrol/blob/v5.1.4/src/Vehicle/FactGroups/VehicleGPSFactGroup.cc).

| # | Trường QGC | Đơn vị | Vai trò | Nguồn / xử lý | Mục đích |
|---:|---|---|---|---|---|
| 13 | `gps.lat` | deg | S | `GPS_RAW_INT.lat / 1e7` | Vĩ độ GPS thô; đối chiếu định vị, không mặc định là vị trí hợp nhất. |
| 14 | `gps.lon` | deg | S | `GPS_RAW_INT.lon / 1e7` | Kinh độ GPS thô; đối chiếu định vị. |
| 15 | `gps.hdop` | không đơn vị | S | `GPS_RAW_INT.eph / 100` | Chỉ số chất lượng hình học ngang của GPS, không phải sai số mét. |
| 16 | `gps.vdop` | không đơn vị | S | `GPS_RAW_INT.epv / 100` | Chỉ số chất lượng hình học đứng của GPS, không phải sai số mét. |
| 17 | `gps.lock` | enum | S* | `GPS_RAW_INT.fix_type` | Kiểm tra loại fix khi hệ thống dùng GNSS/RTK. |
| 18 | `gps.count` | vệ tinh | S | `GPS_RAW_INT.satellites_visible` | Giám sát vệ tinh; số lượng không tự bảo đảm độ chính xác. |

### Vị trí/vận tốc local thực tế — 6 trường

Mapping được đối chiếu trong [VehicleLocalPositionFactGroup.cc](https://github.com/mavlink/qgroundcontrol/blob/v5.1.4/src/Vehicle/FactGroups/VehicleLocalPositionFactGroup.cc).

| # | Trường QGC | Đơn vị | Vai trò | Nguồn / xử lý | Mục đích |
|---:|---|---|---|---|---|
| 19 | `localPosition.x` | m | C* | `LOCAL_POSITION_NED.x` | Vị trí North sau khi quy về origin chung. |
| 20 | `localPosition.y` | m | C* | `LOCAL_POSITION_NED.y` | Vị trí East sau khi quy về origin chung. |
| 21 | `localPosition.z` | m | C* | `LOCAL_POSITION_NED.z` | Vị trí Down sau khi quy về origin chung. |
| 22 | `localPosition.vx` | m/s | C* | `LOCAL_POSITION_NED.vx` | Vận tốc North; dùng khi thuật toán cần vận tốc/feed-forward. |
| 23 | `localPosition.vy` | m/s | C* | `LOCAL_POSITION_NED.vy` | Vận tốc East; dùng khi thuật toán cần vận tốc/feed-forward. |
| 24 | `localPosition.vz` | m/s | C* | `LOCAL_POSITION_NED.vz` | Vận tốc Down; dùng khi thuật toán cần vận tốc/feed-forward. |

### Setpoint autopilot báo lại — 6 trường

Mapping được đối chiếu trong [VehicleLocalPositionSetpointFactGroup.cc](https://github.com/mavlink/qgroundcontrol/blob/v5.1.4/src/Vehicle/FactGroups/VehicleLocalPositionSetpointFactGroup.cc).

| # | Trường QGC | Đơn vị | Vai trò | Nguồn / xử lý | Mục đích |
|---:|---|---|---|---|---|
| 25 | `localPositionSetpoint.x` | m | D | `POSITION_TARGET_LOCAL_NED.x` | Target North autopilot báo lại, nếu firmware hỗ trợ. |
| 26 | `localPositionSetpoint.y` | m | D | `POSITION_TARGET_LOCAL_NED.y` | Target East autopilot báo lại. |
| 27 | `localPositionSetpoint.z` | m | D | `POSITION_TARGET_LOCAL_NED.z` | Target Down autopilot báo lại. |
| 28 | `localPositionSetpoint.vx` | m/s | D | `POSITION_TARGET_LOCAL_NED.vx` | Target vận tốc North autopilot báo lại. |
| 29 | `localPositionSetpoint.vy` | m/s | D | `POSITION_TARGET_LOCAL_NED.vy` | Target vận tốc East autopilot báo lại. |
| 30 | `localPositionSetpoint.vz` | m/s | D | `POSITION_TARGET_LOCAL_NED.vz` | Target vận tốc Down autopilot báo lại. |

### Chất lượng state estimate — 14 trường

Mapping được đối chiếu trong [VehicleEstimatorStatusFactGroup.cc](https://github.com/mavlink/qgroundcontrol/blob/v5.1.4/src/Vehicle/FactGroups/VehicleEstimatorStatusFactGroup.cc).

| # | Trường QGC | Đơn vị | Vai trò | Nguồn / xử lý | Mục đích |
|---:|---|---|---|---|---|
| 31 | `estimatorStatus.goodAttitudeEsimate` | bool | S | `ESTIMATOR_STATUS.flags: ESTIMATOR_ATTITUDE` | Chất lượng attitude; giữ đúng tên metadata QGC có lỗi chính tả. |
| 32 | `estimatorStatus.goodHorizVelEstimate` | bool | S* | `ESTIMATOR_STATUS.flags: ESTIMATOR_VELOCITY_HORIZ` | Kiểm tra vận tốc ngang hợp lệ khi dùng vận tốc. |
| 33 | `estimatorStatus.goodVertVelEstimate` | bool | S* | `ESTIMATOR_STATUS.flags: ESTIMATOR_VELOCITY_VERT` | Kiểm tra vận tốc đứng hợp lệ khi dùng vận tốc. |
| 34 | `estimatorStatus.goodHorizPosRelEstimate` | bool | S* | `ESTIMATOR_STATUS.flags: ESTIMATOR_POS_HORIZ_REL` | Kiểm tra vị trí local hợp lệ khi dùng local position. |
| 35 | `estimatorStatus.goodHorizPosAbsEstimate` | bool | S* | `ESTIMATOR_STATUS.flags: ESTIMATOR_POS_HORIZ_ABS` | Kiểm tra vị trí toàn cục hợp lệ khi dùng global position. |
| 36 | `estimatorStatus.goodVertPosAbsEstimate` | bool | S* | `ESTIMATOR_STATUS.flags: ESTIMATOR_POS_VERT_ABS` | Kiểm tra ước lượng độ cao tuyệt đối. |
| 37 | `estimatorStatus.gpsGlitch` | bool | S* | `ESTIMATOR_STATUS.flags: ESTIMATOR_GPS_GLITCH` | Phát hiện định vị GPS bất thường khi dùng GNSS. |
| 38 | `estimatorStatus.accelError` | bool | S | `ESTIMATOR_STATUS.flags: ESTIMATOR_ACCEL_ERROR` | Cảnh báo bất thường gia tốc ảnh hưởng state estimate. |
| 39 | `estimatorStatus.velRatio` | tỷ số | D | `ESTIMATOR_STATUS.vel_ratio` | Chẩn đoán consistency của ước lượng vận tốc. |
| 40 | `estimatorStatus.horizPosRatio` | tỷ số | D | `ESTIMATOR_STATUS.pos_horiz_ratio` | Chẩn đoán consistency của vị trí ngang. |
| 41 | `estimatorStatus.vertPosRatio` | tỷ số | D | `ESTIMATOR_STATUS.pos_vert_ratio` | Chẩn đoán consistency của vị trí đứng. |
| 42 | `estimatorStatus.magRatio` | tỷ số | D | `ESTIMATOR_STATUS.mag_ratio` | Chẩn đoán consistency của từ kế/hướng. |
| 43 | `estimatorStatus.horizPosAccuracy` | m | S* | `ESTIMATOR_STATUS.pos_horiz_accuracy` | Đánh giá độ bất định ngang so với khoảng cách đội hình. |
| 44 | `estimatorStatus.vertPosAccuracy` | m | S* | `ESTIMATOR_STATUS.pos_vert_accuracy` | Đánh giá độ bất định đứng so với khoảng cách đội hình. |

### Pin — 8 trường

Mapping được đối chiếu trong [BatteryFactGroupListModel.cc](https://github.com/mavlink/qgroundcontrol/blob/v5.1.4/src/Vehicle/FactGroups/BatteryFactGroupListModel.cc).

| # | Trường QGC | Đơn vị | Vai trò | Nguồn / xử lý | Mục đích |
|---:|---|---|---|---|---|
| 45 | `battery.id` | ID | S | `BATTERY_STATUS.id` | Gắn dữ liệu vào đúng bộ pin trên đúng drone. |
| 46 | `battery.voltage` | V | S | `BATTERY_STATUS.voltages và voltages_ext, QGC cộng điện áp hợp lệ / 1000` | Theo dõi điện áp nguồn. |
| 47 | `battery.current` | A | S | `BATTERY_STATUS.current_battery / 100` | Theo dõi dòng điện. |
| 48 | `battery.percentRemaining` | % | S | `BATTERY_STATUS.battery_remaining` | Giám sát mức pin, quyết định rời đội/hạ cánh theo policy. |
| 49 | `battery.mahConsumed` | mAh | D | `BATTERY_STATUS.current_consumed` | Phân tích năng lượng đã tiêu thụ. |
| 50 | `battery.temperature` | °C | S | `BATTERY_STATUS.temperature / 100` | Giám sát nhiệt độ pin khi có cảm biến. |
| 51 | `battery.timeRemaining` | s | S | `BATTERY_STATUS.time_remaining` | Thời gian pin ước lượng nếu có; không dùng như bảo đảm thời gian bay. |
| 52 | `battery.chargeState` | enum | S | `BATTERY_STATUS.charge_state` | Cảnh báo pin low/critical/emergency khi được hỗ trợ. |

Lưu ý: `goodAttitudeEsimate` là tên trong JSON QGC; API C++ dùng `goodAttitudeEstimate()`. Ứng dụng có thể chuẩn hóa lại tên khi xây schema riêng.

## 3. Các dữ liệu bắt buộc bổ sung ngoài phép đếm Fact

52 trường trên chưa đủ để quản lý quyền điều khiển và độ mới dữ liệu. Cần thêm các trường sau; không cộng chúng vào tử số 52 vì không cùng cách đếm metadata:

| Dữ liệu | Nguồn / cách tạo | Vai trò |
|---|---|---|
| `system_id`, `component_id` | Header MAVLink hoặc ID tương đương của giao thức riêng | Định danh nguồn và chọn đúng target. |
| `armed`, `flight_mode` | `HEARTBEAT.base_mode/custom_mode`, adapter firmware | Xác định drone đang armed và mode có nhận giá trị đặt không. |
| `system_status` | `HEARTBEAT.system_status` | Trạng thái hệ thống tổng quát; không thay thế chi tiết failsafe. |
| `landed_state` | `EXTENDED_SYS_STATE.landed_state` | Phân biệt đang bay, hạ cánh và trên mặt đất. |
| `sensors_present/enabled/health` | `SYS_STATUS` và nguồn health tương ứng firmware | Kiểm tra cảm biến, pre-arm và subsystem cần dùng. |
| `position_global`, `velocity_global` | `GLOBAL_POSITION_INT.lat/lon/alt/vx/vy/vz` | Nguồn vị trí/vận tốc hợp nhất khi chọn tuyến global; khác GPS thô. |
| `home_position` | `HOME_POSITION` hoặc nguồn tương đương | Giám sát mốc home và hành vi RTL nếu được hỗ trợ. |
| `local_origin/frame_transform` | Origin do bộ điều khiển công bố hoặc phép hiệu chuẩn chung | Chuyển local frame từng drone về frame đội hình; home không mặc nhiên bằng local origin. |
| `source_timestamp`, `sequence` | Payload thời gian và header từng nguồn | Nhận biết gói cũ/đảo thứ tự và khởi động lại nguồn. |
| `receive_time`, `heartbeat_age`, `state_age` | Máy tính ghi bằng clock monotonic | Phát hiện mất link hoặc state ngừng cập nhật dù heartbeat vẫn còn. |
| `command_result` | `COMMAND_ACK.command/result/progress` khi dùng MAVLink command | Phân biệt gửi thành công, bị từ chối và đang thực hiện. |
| `status_text/severity` | `STATUSTEXT` hoặc event/health report của firmware | Hiển thị nguyên nhân lỗi và điều kiện từ chối lệnh. |

Phần Vehicle xử lý heartbeat, global position, system health, landed state, home và command ACK được đối chiếu trong [Vehicle.cc](https://github.com/mavlink/qgroundcontrol/blob/v5.1.4/src/Vehicle/Vehicle.cc); các property ở [Vehicle.h](https://github.com/mavlink/qgroundcontrol/blob/v5.1.4/src/Vehicle/Vehicle.h). Trạng thái mất liên lạc có xử lý riêng trong [VehicleLinkManager.cc](https://github.com/mavlink/qgroundcontrol/blob/v5.1.4/src/Vehicle/VehicleLinkManager.cc). Một số trường ở bảng trên là dữ liệu ứng dụng cần bổ sung từ giao thức, không phải Fact có sẵn trong 7 nhóm đã đếm.

## 4. Quy tắc dùng dữ liệu cho leader–follower

1. **Chọn nguồn position trước:** dùng state hợp nhất từ `LOCAL_POSITION_NED` hoặc `GLOBAL_POSITION_INT` khi thích hợp. `gps.lat/lon` trong QGC là GPS thô, không mặc nhiên là state điều khiển tốt nhất.
2. **Quy về frame chung:** NED có X = North, Y = East, Z = Down. Không lấy local position của các drone có origin khác nhau cộng/trừ trực tiếp. Độ cao relative của hai drone có thể khác mốc.
3. **Giữ độ chính xác:** dùng yaw đầy đủ từ dữ liệu gốc khi tính toán; `heading` trong VehicleFactGroup được chuẩn hóa để hiển thị và làm tròn xuống độ nguyên. Đổi đúng rad ↔ deg, mm/cm ↔ m.
4. **Tách state và target:** `LOCAL_POSITION_NED` là state thực; `POSITION_TARGET_LOCAL_NED` là target autopilot báo lại. Khi gửi dùng `SET_POSITION_TARGET_LOCAL_NED` nếu controller hỗ trợ, với đúng target ID, `coordinate_frame` và `type_mask`. Chỉ dùng các thành phần target đang có hiệu lực.
5. **Không chờ ACK cho từng setpoint stream:** `COMMAND_ACK` thuộc command protocol; target báo lại không chứng minh drone đã tới đích. Phải theo dõi state thực và sai số bám.
6. **Kiểm tra thời gian:** boot time của mỗi drone có mốc riêng. Không lấy timestamp hai drone trừ trực tiếp nếu chưa đồng bộ/ước lượng offset. Đánh dấu stale theo từng stream, không chỉ theo heartbeat.
7. **Xử lý dữ liệu thiếu:** giá trị sentinel/unknown thành `None/NaN` kèm validity, không tự đổi thành 0. Đối chiếu capability; nếu thiếu nguồn state thiết yếu thì ngừng bám, còn chỉ thiếu trường chẩn đoán thì ghi unavailable.
8. **Tự tính tại máy tính:** role leader/follower, offset, target đã gửi, sai số bám XYZ, khoảng cách giữa drone và tuổi target là dữ liệu ứng dụng; không phải tham số QGC gửi sẵn.

Mapping message, hệ tọa độ, đơn vị và sentinel tham khảo [MAVLink Common Messages](https://mavlink.io/en/messages/common.html); ACK tham khảo [Command Protocol](https://mavlink.io/en/services/command.html). Nếu drone dùng giao thức riêng, adapter cần cung cấp ý nghĩa dữ liệu tương đương.

## 5. Các trường chưa chọn và lý do

Trong mẫu 95 trường, bỏ 43 trường sau:

| Nhóm | Trường chưa chọn |
|---|---|
| `vehicle` (15) | `airSpeed`, `altitudeAboveTerr`, `flightDistance`, `distanceToHome`, `timeToHome`, `headingToHome`, `headingFromHome`, `headingFromGCS`, `distanceToGCS`, `missionItemIndex`, `headingToNextWP`, `distanceToNextWP`, `hobbs`, `imuTemp`, `rcRSSI` |
| `gps` (11) | `mgrs`, `courseOverGround`, `yaw`, `systemErrors`, `spoofingState`, `jammingState`, `authenticationState`, `correctionsQuality`, `systemQuality`, `gnssSignalQuality`, `postProcessingQuality` |
| `estimatorStatus` (6) | `goodVertPosAGLEstimate`, `goodConstPosModeEstimate`, `goodPredHorizPosRelEstimate`, `goodPredHorizPosAbsEstimate`, `haglRatio`, `tasRatio` |
| `battery` (4) | `batteryFunction`, `batteryType`, `instantPower`, `timeRemainingStr` |
| `radioStatus` (7) | `lrssi`, `rrssi`, `rxErrors`, `fixed`, `txBuffer`, `lNoise`, `rNoise` |

Lý do: ưu tiên state phục vụ bám và giám sát; bỏ dữ liệu mission/waypoint, hiển thị trùng lặp, hướng về GCS/home và thông tin GNSS nâng cao chưa cần ở bản đầu. Không chọn `radioStatus` vì module Wi-Fi qua router không mặc nhiên phát `RADIO_STATUS`; cũng không dùng RC RSSI thay cho chất lượng Wi-Fi. Link health trước mắt dựa trên heartbeat age, state age và thống kê gói.

Ngoài mẫu so sánh, 13 schema còn lại có 93 định nghĩa metadata: attitude setpoint (6), vibration (6), clock (3), wind (3), distance sensor (12), temperature (3), ESC (10), terrain (2), EFI (19), generator (11), hygrometer (3), RPM (6), submarine (9). Có thể bổ sung sau nếu phần cứng hoặc nhiệm vụ cần; đây không phải khẳng định chúng vô ích.

**Ưu tiên triển khai:** position/velocity hợp lệ trong frame chung → ID/mode/armed/time/health → pin và lỗi → target feedback → dữ liệu chẩn đoán. Không yêu cầu cả 52 Fact đều phải có mới cho phép chạy thuật toán.
