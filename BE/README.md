# CCCD and shared employee-record application

Flask serves both the frontend and API in local development. The shared
database defaults to SQLite at `BE/instance/hr_profiles.sqlite3`; production
production must use PostgreSQL. CCCD OCR uses the configured OpenAI vision API;
portrait processing uses OpenCV's YuNet face detector
(`BE/models/face_detection_yunet_2023mar.onnx`) with a Haar Cascade fallback.

## Local setup

From PowerShell:

```powershell
cd D:\ANVIETRESOURCE\BE
py -3.11 -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

Keep the existing `.env`; do not overwrite it with the example. Configure a
valid `OPENAI_API_KEY`, `OPENAI_VISION_MODEL`, and a long random `SECRET_KEY`
in the private `.env`. API keys must remain backend-only.

Initialize an empty database and create the first admin account:

```powershell
flask --app app db upgrade
flask --app app create-user
```

The account password is created by you, is not your email-provider password,
and must have at least 12 characters. `create-user` asks for the password
twice; terminal input is intentionally not echoed. Run it again only when you
need another account.

Start the local app:

```powershell
python app.py
```

Open `http://localhost:8000/` and sign in. The same Flask process serves FE
assets, OCR/portrait APIs, and shared record APIs. Stop it with `Ctrl+C`.

### Dùng nhiều thiết bị trong cùng mạng Wi-Fi/LAN

Một máy tính làm máy chủ và giữ cơ sở dữ liệu SQLite; các máy/điện thoại khác
mở ứng dụng bằng địa chỉ IP nội bộ của máy chủ. Máy chủ phải luôn bật trong
thời gian mọi người sử dụng. Không mở cổng router ra Internet.

Trên máy chủ, lấy địa chỉ IPv4 và độ dài tiền tố của card mạng đang kết nối:

```powershell
Get-NetIPAddress -AddressFamily IPv4 |
  Where-Object { $_.IPAddress -notlike "127.*" -and $_.AddressState -eq "Preferred" } |
  Select-Object IPAddress, PrefixLength, InterfaceAlias
```

Trong `BE/.env`, đặt các giá trị theo mạng thực tế (ví dụ dưới đây cần thay
IP, CIDR và origin theo kết quả của máy chủ/router):

```dotenv
HOST=0.0.0.0
LOCAL_ONLY=false
ALLOWED_CLIENT_NETWORKS=192.168.1.0/24
ALLOWED_ORIGINS=http://192.168.1.25:8000,http://localhost:8000,http://127.0.0.1:8000
SESSION_COOKIE_SECURE=false
```

`ALLOWED_CLIENT_NETWORKS` giới hạn API vào subnet nội bộ đã khai báo; khi bật
truy cập mạng trong development mà chưa khai báo subnet, API từ thiết bị khác
sẽ bị từ chối. `ALLOWED_ORIGINS` phải chứa đúng origin dùng để mở trang, gồm
scheme và port. Khởi động lại backend sau khi sửa `.env`, cho phép Python qua
Windows Firewall trên **Private networks** nếu được hỏi, rồi mở
`http://<IPv4-máy-chủ>:8000/` trên thiết bị cùng Wi-Fi. Nếu router bật client
isolation/guest network, hãy dùng mạng nội bộ cho phép các thiết bị nhìn thấy
nhau. Tài khoản vẫn phải đăng nhập; hãy tạo tài khoản riêng cho từng nhân viên.

HTTP qua Wi-Fi phù hợp để thử lưu hồ sơ và tải ảnh lên. Camera trực tiếp trên
điện thoại thường yêu cầu HTTPS; cần cấu hình HTTPS trước khi dùng camera.

CCCD processing keeps the sequence OCR of both original images, QR decoding
with ZXing-C++ (full-image variants followed by localized, upscaled QR crops),
supported-field correction, then TD1 MRZ structure/check-digit validation and
comparison. Localized QR crops help decode codes that occupy only a small part
of a full-card photo. QR results report whether they match OCR or which fields
they corrected. MRZ comparison includes the
12-digit identity number in the optional field and the accent-insensitive name.
OCR image inputs use high-detail vision processing. Before check-digit validation,
the verifier only restores or removes MRZ filler characters (`<`) to account for
OCR length errors; it never changes letters or digits. It also warns about
inconsistent 12-digit ID structure, missing required fields, and invalid date
relationships so users can review them without silently replacing OCR data.
The combined place field reads “Quê quán / Place of origin” on older cards and
“Nơi đăng ký khai sinh / Place of birth” on newer cards; residence is never used
as a substitute.
A valid but mismatching MRZ produces review suggestions only; it never
overwrites OCR/QR values. The scan form shows QR/MRZ states and requires an
explicit review confirmation before saving OCR results.

If the database tables already exist but Alembic has no current revision, take
a backup and verify the existing tables and constraints match this schema
before stamping it. `db check` requires Alembic to already be at the current
revision, so run it after the stamp:

```powershell
flask --app app db stamp head
flask --app app db check
```

Stamping only records the revision in `alembic_version`; it does not create,
alter, or delete the existing tables or records. Do not stamp an unverified
database.

Do not run `db downgrade` on a database containing records. Keep backups of
`BE/instance/hr_profiles.sqlite3`; it contains identity information and images.

## Accounts and storage

All folder/profile and OCR/portrait API calls require an authenticated session.
Sessions use HttpOnly cookies; modifying requests also require a CSRF token.
Use the backend CLI to create an employee account, then share its login details
through a secure channel:

```powershell
flask --app app create-user
```

Choose the `employee` role when prompted. Employee access is checked by the
backend, not just hidden in the interface.

Folders, structured profile data, and PNG/JPEG images are stored in the shared
database. A normalized identity number is unique. Each folder is limited to 50
profiles. Profile updates use a version check; stale updates return HTTP 409
instead of replacing a newer edit.

Old browser data cannot be read by the server automatically. Sign in on each
browser containing legacy data, open **Sao lưu dữ liệu cũ**, export its JSON,
then have an admin preview and import the file. Export and import do not erase
the browser source. Treat backup files as sensitive.

## Production notes

Use PostgreSQL and a production WSGI server such as Gunicorn; never run Flask's
development server in production. Set `APP_ENV=production`, a strong
`SECRET_KEY`, `DATABASE_URL`, exact HTTPS `ALLOWED_ORIGINS`,
`LOCAL_ONLY=false`, and `SESSION_COOKIE_SECURE=true`. Configure frontend and
API on the same site (for example, `app.example.com` and `api.example.com`) for
secure cookie authentication. Keep secrets and database credentials out of
frontend files. Review OpenAI's data processing and retention terms before
sending real identity documents.

## Deploying the full application to Render

The repository-root `render.yaml` defines a paid Render Web Service with
2 GB RAM and a PostgreSQL database with 1 GB RAM and 5 GB initial storage, in
the Singapore region. The service runs from the
repository root so Flask can serve both `BE/` and `FE/`; it does not package
the backend as a serverless function. The configured web plan has 2 GB RAM
for the image-processing dependencies. Confirm current plan pricing
and resource limits in Render before applying the Blueprint. Increase the web
plan if Render logs show out-of-memory errors during startup or image
processing.

Before deploying:

1. Review the files to be pushed and push the intended changes to the `main`
   branch of `phuongleevt03/ANVIETRESOURCE`. Do not commit `.env`, API keys,
   database URLs, local databases, or real identity data.
2. Open Render Dashboard and choose **New > Blueprint**. Connect
   `phuongleevt03/ANVIETRESOURCE`, select `main`, and review the paid resources
   and costs shown by Render before applying the Blueprint.
3. Keep the web service name `anvietresource` so its HTTPS origin matches
   `ALLOWED_ORIGINS` in `render.yaml`. If that name is unavailable or changed,
   update `ALLOWED_ORIGINS` in the Render service environment to the exact
   HTTPS URL Render assigns; do not add a trailing slash.
4. Enter `OPENAI_API_KEY` in Render when prompted or in the service's
   **Environment** page. The key is intentionally not stored in this file.
   Render generates `SECRET_KEY`; the Blueprint connects the service to its
   PostgreSQL database and runs `flask --app app db upgrade` before starting
   Gunicorn.

After the first successful deploy, open the web service's Shell and create the
first administrator:

```bash
cd BE
flask --app app create-user
```

Use the interactive prompt to create an admin password of at least 12
characters. Open the Render service URL, verify `/api/health`, then test login,
record creation, OCR, QR/MRZ verification, and portrait cropping with
non-sensitive test data. Review Render logs and metrics for build, startup, or
memory errors.

Render PostgreSQL is separate from the local SQLite database. Existing local
records are not copied automatically. Keep local `BE/.env` and local SQLite
configuration separate from Render environment variables; local development
continues to use the setup described above. Verify Render's current storage,
backup, retention, and pricing terms before using real identity data.

## Tests

From `BE/`:

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

The focused document-verification tests also run with
`.\venv\Scripts\python.exe -m unittest tests.test_document_verification -v`.

## Deploying to Vercel (serverless)

`.vercelignore` at the repository root keeps raw address data, tests, and
local-only files out of the upload. Serverless disks are wiped after each
invocation, so SQLite cannot be used: with `APP_ENV=production` the app refuses
to start unless `DATABASE_URL` points to PostgreSQL. Set these environment
variables in the Vercel project:

- `APP_ENV=production`
- `DATABASE_URL` (PostgreSQL connection string)
- `SECRET_KEY`
- `ALLOWED_ORIGINS=https://<domain>` (exact origin, no trailing slash)
- `SESSION_COOKIE_SECURE=true`
- `OPENAI_API_KEY`

Then run the migrations once against that database from a machine with the
same `DATABASE_URL`:

```bash
cd BE
flask --app app db upgrade
```

Vercel limits each request body to about 4.5 MB, so the frontend resizes CCCD
images to at most 1600 px on the long edge and re-encodes them as JPEG (quality
0.85) before upload.
