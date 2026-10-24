# Deploy lên Vercel (giao diện) + Render (máy chủ)

Hướng dẫn đưa giao diện (thư mục `FE/`) lên [Vercel](https://vercel.com) và máy chủ quét CCCD,
quản lý hồ sơ (thư mục `BE/`) cùng cơ sở dữ liệu PostgreSQL lên [Render](https://render.com).

## Tổng quan

```
Trình duyệt ──> Vercel (giao diện FE/, cùng domain cho cả /api)
                   │  /api/* được Vercel chuyển tiếp (rewrite trong vercel.json)
                   ▼
              Render Web Service (BE/, Flask + gunicorn)
                   │                      │
                   ▼                      ▼
          Render PostgreSQL        OpenAI → Groq tùy chọn (OCR dự phòng)
```

Khác với repo `cmnd`, giao diện **không gọi thẳng** sang domain Render. Ứng dụng này đăng nhập
bằng cookie phiên + CSRF; nếu gọi chéo domain (`*.vercel.app` → `*.onrender.com`) thì trình duyệt
coi là cookie bên thứ ba và Safari/iOS sẽ chặn, không đăng nhập được. Vercel chuyển tiếp `/api/*`
sang Render nên với trình duyệt mọi thứ nằm trên **một domain**: không cần CORS, không cần biến
`API_BASE`.

Các tệp liên quan:

| Tệp | Vai trò |
|---|---|
| `vercel.json` | Vercel phục vụ thư mục `FE/`, chuyển `/` sang `/folders.html`, chuyển tiếp `/api/*` sang Render |
| `.vercelignore` | Chỉ upload `FE/` lên Vercel; loại `BE/`, dữ liệu địa chỉ gốc, `docs/` |
| `render.yaml` | Render Blueprint: Web Service Python + PostgreSQL, chạy migration trước mỗi lần deploy |
| `BE/requirements.txt` | Thư viện chạy (không có PyTorch/YOLO; tách chân dung dùng OpenCV YuNet) |
| `BE/models/face_detection_yunet_2023mar.onnx` | Model dò khuôn mặt (~230KB) |

## Bước 0. Chuẩn bị

1. Tài khoản Render và Vercel, đăng nhập bằng GitHub.
2. Cho cả hai đọc repo `phuongleevt03/ANVIETRESOURCE` (lần đầu kết nối GitHub chọn
   **Only select repositories** → `ANVIETRESOURCE`).
3. OpenAI API key; Groq API key tại [Groq Console](https://console.groq.com/keys) là tùy chọn
   để dự phòng. **Chỉ dán key vào màn hình Render, không ghi vào tệp nào trong repo.**
4. Quyết định domain giao diện: mặc định Vercel cấp `https://<tên-project>.vercel.app`
   (xem Vercel → Project → **Settings** → **Domains**). Cần biết domain này cho `ALLOWED_ORIGINS`.

## Bước 1. Render: tạo máy chủ + cơ sở dữ liệu

### Cách A (khuyên dùng): Blueprint từ `render.yaml`

1. Dashboard → **New** → **Blueprint**.
2. Chọn repo `phuongleevt03/ANVIETRESOURCE`, nhánh `main`. Render tự đọc `render.yaml`.
3. Render hỏi giá trị cho các biến đánh dấu `sync: false`:
   - `OPENAI_API_KEY`: dán OpenAI key.
   - `GROQ_API_KEY`: chỉ nhập nếu đồng ý cho phép gửi ảnh CCCD tới Groq khi OpenAI lỗi.
   - `ALLOWED_ORIGINS`: `https://<domain-vercel>,https://anvietresource.onrender.com`
     (cách nhau bằng dấu phẩy, **không có `/` ở cuối**).
4. Xem lại gói và chi phí Render hiển thị, bấm **Apply**.

Blueprint tạo sẵn:

- Web Service `anvietresource` (Python 3.11, vùng Singapore, gói `1c-2g`), chạy
  `gunicorn --workers 1 --threads 2 --timeout 120`, kiểm tra sức khoẻ ở `/api/health`.
- PostgreSQL `anvietresource-db` (gói `0.5c-1g`, 5GB), tự nối vào biến `DATABASE_URL`.
- `SECRET_KEY` sinh ngẫu nhiên.
- Trước mỗi lần deploy chạy `flask --app app db upgrade` (tạo/cập nhật bảng).

Từ khi bỏ YOLO, máy chủ cần ít RAM hơn nhiều; có thể hạ gói Web Service nếu Metrics cho thấy
RAM dùng thấp. Lưu ý gói Free của Render **không có** Pre-Deploy Command và Shell, khi đó phải
tự chạy migration và tạo tài khoản từ máy mình (xem Bước 3).

### Cách B: tạo tay

1. **New** → **PostgreSQL**: Name `anvietresource-db`, Region `Singapore`. Tạo xong, chép
   **Internal Database URL**.
2. **New** → **Web Service** → chọn repo, rồi điền:

| Mục | Giá trị |
|---|---|
| Name | `anvietresource` |
| Language | `Python 3` |
| Branch | `main` |
| Region | `Singapore` (cùng vùng với database) |
| Root Directory | để trống (gốc repo) |
| Build Command | `pip install -r BE/requirements.txt` |
| Pre-Deploy Command | `cd BE && flask --app app db upgrade` |
| Start Command | `gunicorn --chdir BE --workers 1 --threads 2 --timeout 120 --bind 0.0.0.0:$PORT app:app` |
| Health Check Path | `/api/health` |

Thêm **Environment Variables** như bảng ở Bước 2, rồi bấm **Deploy Web Service**.

## Bước 2. Biến môi trường trên Render

| Biến | Bắt buộc | Giá trị | Ghi chú |
|---|---|---|---|
| `APP_ENV` | Có | `production` | Bật các kiểm tra an toàn khi khởi động |
| `DATABASE_URL` | Có | URL PostgreSQL | Blueprint tự nối. Thiếu hoặc là SQLite thì máy chủ **không khởi động** (báo `Production yêu cầu DATABASE_URL trỏ tới PostgreSQL`) |
| `SECRET_KEY` | Có | chuỗi ngẫu nhiên dài | Blueprint tự sinh. Đổi giá trị = mọi người bị đăng xuất |
| `ALLOWED_ORIGINS` | Có | `https://<domain-vercel>,https://anvietresource.onrender.com` | Phải là `https://`, không có `/` cuối |
| `SESSION_COOKIE_SECURE` | Có | `true` | Cookie phiên chỉ gửi qua HTTPS |
| `OPENAI_API_KEY` | Khuyến nghị | OpenAI key | Nhà cung cấp OCR chính |
| `OPENAI_VISION_MODEL` | Không | `gpt-6-luna` | Model OpenAI đọc ảnh CCCD |
| `OCR_PROVIDERS` | Không | `openai,groq` | Thứ tự thử OCR; provider không có key sẽ được bỏ qua |
| `GROQ_API_KEY` | Không | Groq key | Ảnh CCCD được gửi tới Groq nếu các provider đứng trước thất bại |
| `GROQ_VISION_MODEL` | Không | `qwen/qwen3.8-27b` | Model Groq dự phòng |
| `LOCAL_ONLY` | Không | `false` | Production mặc định đã là `false` |
| `PYTHON_VERSION` | Không | `3.11.11` | |
| `PORT` | Không | Render tự đặt | Không khai báo |

Đổi biến xong bấm **Save Changes**, Render sẽ khởi động lại.

> Chỉ cấu hình `GROQ_API_KEY` nếu bạn đồng ý để ảnh CCCD được gửi tới Groq
> khi OpenAI lỗi. Không đặt key này thì hệ thống chỉ gọi OpenAI.

## Bước 3. Kiểm tra máy chủ và tạo tài khoản quản trị

Render cấp địa chỉ `https://anvietresource.onrender.com` (xem ở đầu trang dịch vụ).

```bash
curl https://anvietresource.onrender.com/api/health
```

Kết quả đúng có `"status":"ok"` và `"success":true`; trường `ocrProviders` cho biết thứ tự
provider có key đã được nhận (không hiển thị key).

Tạo tài khoản quản trị đầu tiên: trang dịch vụ → **Shell**:

```bash
cd BE
flask --app app create-user
```

Nhập email, vai trò `admin`, mật khẩu ít nhất 12 ký tự.

Gói không có Shell: chạy từ máy mình với **External Database URL** của Render (Database →
**Connect**). Đặt biến này trong chính lệnh, không ghi vào `BE/.env`:

```bash
cd BE && APP_ENV=production SECRET_KEY=tam ALLOWED_ORIGINS=https://x DATABASE_URL='<external-database-url>' venv/bin/flask --app app db upgrade
```

```bash
cd BE && APP_ENV=production SECRET_KEY=tam ALLOWED_ORIGINS=https://x DATABASE_URL='<external-database-url>' venv/bin/flask --app app create-user
```

## Bước 4. Vercel: đưa giao diện lên

1. Vercel → **Add New** → **Project** → chọn repo `phuongleevt03/ANVIETRESOURCE`.
2. Cấu hình (đã có sẵn trong `vercel.json`, chỉ cần kiểm tra):

| Mục | Giá trị |
|---|---|
| Framework Preset | `Other` |
| Root Directory | `./` (gốc repo) |
| Build Command | để trống |
| Output Directory | `FE` |
| Environment Variables | không cần |

3. Bấm **Deploy**. Vercel chỉ upload `FE/` (xem `.vercelignore`) nên deploy mất vài giây.
4. Nếu tên dịch vụ Render **không phải** `anvietresource`, sửa `destination` trong `vercel.json`
   thành đúng địa chỉ Render, commit và push:

```json
{ "source": "/api/:path*", "destination": "https://<tên-dịch-vụ>.onrender.com/api/:path*" }
```

5. Kiểm tra chuyển tiếp: mở `https://<domain-vercel>/api/health`, phải ra cùng kết quả như Bước 3.
6. Mở `https://<domain-vercel>`, đăng nhập bằng tài khoản vừa tạo, thử tải ảnh mặt trước:
   khung avatar hiện chân dung là đã nối thành công. Thử tiếp quét mặt sau và lưu hồ sơ.

### Cấu hình `ALLOWED_ORIGINS` (nguyên nhân số 1 của lỗi 403)

Máy chủ so trình duyệt gửi kèm header `Origin` với danh sách này, **khớp chính xác từng chữ**:

- Thêm domain Vercel production (và domain riêng nếu có gắn).
- Giữ `https://anvietresource.onrender.com` nếu muốn dùng trực tiếp giao diện trên Render
  (Flask vẫn phục vụ được `FE/`).
- **URL preview** của Vercel (`<project>-<chuỗi-ngẫu-nhiên>.vercel.app`) đổi mỗi lần deploy và
  không khớp, nên các thao tác ghi (đăng nhập, lưu, quét) trên preview sẽ bị từ chối
  `ORIGIN_NOT_ALLOWED`. Thử nghiệm trên domain production, hoặc thêm tạm URL preview vào danh sách.

## Giới hạn cần biết

- **Render Free ngủ khi không có truy cập**: request đầu tiên khi mở trang sẽ tự đánh thức máy chủ.
  Giao diện chờ và tự thử lại kiểm tra phiên khi gặp lỗi mạng hoặc HTTP `502`/`503`/`504`
  (tối đa khoảng 83 giây), đồng thời hiện thông báo trong lúc chờ. Không cần ping định kỳ;
  nếu Render chưa sẵn sàng sau thời gian này, hãy đợi thêm rồi tải lại trang.
- **Ảnh tải lên**: giao diện tự thu nhỏ ảnh CCCD (cạnh dài tối đa 1600px, JPEG chất lượng 0.85)
  trước khi gửi, thường còn vài trăm KB mỗi ảnh. Máy chủ chấp nhận tối đa 10MB mỗi yêu cầu;
  ảnh gửi tới OCR được xử lý riêng, tối đa 1600px cạnh dài và 1,5MB mỗi mặt.
- **120 giây**: Vercel chỉ chờ máy chủ Render trả lời tối đa 120 giây cho mỗi yêu cầu chuyển tiếp,
  quá thì trả `502`. gunicorn cũng đặt `--timeout 120`. Mỗi provider OCR có timeout 45 giây,
  để có thể thử provider kế tiếp mà vẫn nằm trong giới hạn request.
- **Không cache API**: máy chủ trả `Cache-Control: no-store` cho mọi `/api/*`, nên Vercel không
  lưu đệm dữ liệu hồ sơ.
- **Dữ liệu cũ**: PostgreSQL trên Render tách biệt với SQLite trên máy (`BE/instance/`), hồ sơ cũ
  không tự chuyển lên.
- **Chi phí**: Blueprint dùng gói trả phí của Render. Kiểm tra giá, dung lượng lưu trữ, sao lưu
  và thời hạn giữ dữ liệu của Render (và gói Vercel phù hợp mục đích thương mại) trước khi đưa
  dữ liệu thật lên.

## Xử lý sự cố

| Hiện tượng | Nguyên nhân thường gặp | Cách xử lý |
|---|---|---|
| Deploy Render thất bại, log có `Production yêu cầu DATABASE_URL trỏ tới PostgreSQL` | Thiếu `DATABASE_URL` hoặc trỏ SQLite | Nối database (Bước 1), lưu lại |
| Log có `SECRET_KEY phải được cấu hình` | Thiếu `SECRET_KEY` | Thêm biến, giá trị ngẫu nhiên dài |
| Log có `Production yêu cầu ALLOWED_ORIGINS là các origin HTTPS` | Có địa chỉ `http://` hoặc để trống | Chỉ dùng `https://...` |
| `403 ORIGIN_NOT_ALLOWED` | Domain đang mở không có trong `ALLOWED_ORIGINS` (hay gặp với URL preview) | Thêm đúng domain, không có `/` cuối, **Save Changes** |
| `403 CSRF_INVALID` / bị đăng xuất liên tục | Cookie phiên không được giữ: mở giao diện bằng `http://`, hoặc `SECRET_KEY` vừa đổi | Dùng `https://`, đăng nhập lại |
| `401 AUTH_REQUIRED` | Chưa đăng nhập hoặc phiên hết hạn (8 giờ) | Đăng nhập lại |
| `503 DATABASE_UNAVAILABLE` | Database đang khởi động/hết dung lượng, hoặc chưa chạy migration | Xem log Render; chạy `flask --app app db upgrade` |
| `503 OCR_NOT_CONFIGURED` | Chưa có key cho provider OCR nào | Cấu hình `OPENAI_API_KEY` hoặc `GROQ_API_KEY` |
| `502 OCR_PROVIDERS_FAILED` | Tất cả provider OCR được cấu hình đều từ chối hoặc không xử lý được yêu cầu | Xem log Render để biết provider và loại lỗi; kiểm tra key/model, hạn mức và thử lại |
| `413 FILE_TOO_LARGE` | Yêu cầu vượt 10MB | Kiểm tra giao diện đã là bản mới (có thu nhỏ ảnh), xoá cache trình duyệt |
| `502` khi quét CCCD qua Vercel | Máy chủ Render trả lời quá 120 giây hoặc đang khởi động lại | Xem Logs Render, thử lại |
| `/api/health` trên Vercel ra `404` | `vercel.json` sai địa chỉ Render hoặc Vercel chưa deploy bản có `vercel.json` | Sửa `destination` (Bước 4), Redeploy |
| Nhập bản sao lưu (Migration) lớn qua Vercel bị lỗi | Tệp sao lưu kèm nhiều ảnh, yêu cầu quá lớn để chuyển tiếp | Mở giao diện trực tiếp trên `https://anvietresource.onrender.com` để nhập |
| Không tìm thấy khuôn mặt | Ảnh mờ, chói, bị cắt mất ảnh chân dung | Chụp lại rõ, đủ sáng, đủ 4 góc thẻ |

Xem log: Render → dịch vụ → **Logs**; Vercel → Project → **Logs** (mục External Rewrites).
Log máy chủ chỉ ghi trạng thái, không ghi dữ liệu cá nhân.

## Bảo mật

- `.env`, API key và URL database không được commit vào repo (đã chặn trong `.gitignore`); chỉ
  nhập trên màn hình Render.
- Vercel không nhận mã nguồn máy chủ (`/BE/` nằm trong `.vercelignore`).
- Mọi `/api/*` (trừ `/api/health`, kiểm tra phiên và đăng nhập) yêu cầu đăng nhập; thao tác ghi cần
  thêm token CSRF; nhập bản sao lưu chỉ dành cho `admin`.
- Máy chủ chỉ nhận yêu cầu từ các địa chỉ trong `ALLOWED_ORIGINS`.
- Ảnh CCCD được gửi tới OpenAI khi đọc thông tin. Nếu đã cấu hình `GROQ_API_KEY`, ảnh cũng có thể
  được gửi tới Groq khi OpenAI lỗi. Xem điều khoản xử lý và lưu giữ dữ liệu của cả hai nhà cung cấp
  trước khi dùng giấy tờ thật.
