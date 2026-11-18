# SMTP Test Tools

Aplikasi terminal Python + Textual untuk mengetes konfigurasi SMTP, mengirim email
percobaan, dan membaca hasil diagnosis. Python 3.13+ diperlukan.

## Menjalankan

```bash
uv sync
uv run smtp-test
```

Tab/Shift+Tab berpindah kontrol; Enter memilih tombol; tombol panah mengubah
pilihan. Ctrl+T mengetes koneksi, Escape meminta pembatalan, Ctrl+Q keluar.

## Penggunaan TUI

- **Profil:** isi host, port, keamanan, timeout, username, dan pengirim. Password
  bisa diisi untuk sesi ini atau dibaca dari environment variable. Simpan dengan
  nama sama untuk memperbarui profil; pilih profil tersimpan untuk duplikasi/hapus.
- **Email:** isi To/Cc/Bcc (alamat sederhana, dipisahkan koma), subjek, teks,
  HTML opsional, envelope sender, dan attachment (satu path per baris).
  Klik **Kirim email** untuk mengirim satu pesan. **Tes koneksi** tidak mengirim.
- **Diagnosis:** periksa MX/SPF/DMARC, bandingkan profil, ulangi tes koneksi,
  atau periksa kebijakan relay tanpa mengirim isi pesan.
- **Mailbox:** isi koneksi IMAP TLS/STARTTLS, folder, dan Message-ID. Message-ID
  otomatis diisi dari pengiriman atau riwayat yang dipilih. Pencarian dilakukan
  satu kali; ulangi manual bila pesan belum tiba.
- **Hasil:** status PASS/FAIL/WARN/SKIP, durasi tiap tahap, dan respons server.
- **Riwayat / ekspor:** pilih laporan JSON, ekspor ke JSON/teks dengan path
  tujuan pilihan sendiri, atau lihat transcript tersensor pada tab Hasil.

Port umum: 587 + STARTTLS, 465 + TLS; port 25 mengikuti kebijakan server.
Port dan mode keamanan diatur secara terpisah.

## Metode pengujian yang tersedia

| Metode | Hasil |
|---|---|
| Validasi konfigurasi | Host, port, timeout, keamanan, alamat, kredensial |
| DNS hostname | Alamat IPv4/IPv6 yang dihasilkan resolver OS |
| TCP dan greeting | Keterjangkauan port, waktu koneksi, banner dan kode 220 |
| EHLO / HELO | Kapabilitas ESMTP, fallback HELO untuk penolakan EHLO tertentu |
| STARTTLS / implicit TLS | Handshake, versi TLS, hostname, trust chain dan kedaluwarsa sertifikat |
| Autentikasi | Login SMTP sesuai mekanisme yang didukung smtplib/server |
| MIME | Teks, HTML + alternatif teks, attachment; maksimal total attachment 25 MiB |
| Penerima | To/Cc/Bcc, hasil RCPT individual, penolakan sebagian penerima |
| Envelope | Pengirim envelope dapat berbeda dari header From |
| Ukuran pesan | Ukuran MIME aktual dibandingkan batas SIZE bila diumumkan |
| ESMTP | Kapabilitas SIZE, SMTPUTF8, 8BITMIME, DSN dan ekstensi lainnya |
| SMTPUTF8 | Alamat internasional membutuhkan SMTPUTF8 dan 8BITMIME |
| DSN | Permintaan RET=HDRS dan NOTIFY=SUCCESS,FAILURE,DELAY bila didukung |
| Respons gagal | Kode respons SMTP, penolakan sementara 4xx dan permanen 5xx |
| Perbandingan | Hasil dan total durasi masing-masing profil; rincian per tahap dalam metadata laporan |
| Stabilitas | Ulangi koneksi 1–20 kali, interval 0–60 detik, tanpa pengiriman |
| Relay policy | MAIL FROM / RCPT TO / RSET; harapkan penerimaan atau penolakan |
| DNS domain | MX, SPF dan DMARC, record hilang/ganda, Null MX, kegagalan resolver |
| Mailbox IMAP | Pencarian Message-ID tepat, UID, header Authentication-Results/Received-SPF, keberadaan DKIM-Signature |

## CLI untuk otomasi

Tanpa subcommand, aplikasi membuka TUI. Gunakan `--help` pada tiap subcommand
untuk semua parameter. Password hanya dibaca dari environment, bukan argumen CLI.

```bash
# Simpan konfigurasi (tanpa password)
uv run smtp-test profiles save staging --host smtp.example.com --port 587 --security STARTTLS --username user@example.com --password-env SMTP_PASSWORD --sender user@example.com

# Isi secret interaktif pada shell bash, lalu ekspor ke proses anak
read -rsp 'SMTP password: ' SMTP_PASSWORD
export SMTP_PASSWORD

uv run smtp-test test --profile staging
uv run smtp-test send --profile staging --to recipient@example.com --subject 'SMTP test' --body 'Hello'
uv run smtp-test send --profile staging --to recipient@example.com --cc other@example.com --bcc hidden@example.com --html-file email.html --attach report.pdf --envelope-sender bounce@example.com --dsn

uv run smtp-test profiles list
uv run smtp-test profiles duplicate staging --copy-name staging-copy
uv run smtp-test profiles delete staging-copy

uv run smtp-test domain example.com
uv run smtp-test compare staging production
uv run smtp-test stability --profile staging --repeats 3 --interval 2
uv run smtp-test relay --profile staging --relay-sender probe@example.com --relay-recipient recipient@other.example --expect reject

uv run smtp-test mailbox --host imap.example.com --username user@example.com --password-env IMAP_PASSWORD --message-id '<id@example.com>'
uv run smtp-test history
uv run smtp-test history /path/to/history/report.json --export /tmp/report.txt --format text
uv run smtp-test test --profile staging --export /tmp/report.json
```

`compare` tanpa nama menggunakan semua profil tersimpan; masing-masing membaca
password dari environment variable profilnya. Di TUI, password sesi juga dapat
memenuhi kredensial suite dengan satu profil.

Exit code CLI: **0** selesai tanpa FAIL (bisa mengandung WARN/SKIP), **1** terdapat
FAIL, **2** konfigurasi/penyimpanan tidak valid, **130** interupsi keyboard.
Output diagnosis berupa JSON, cocok diproses dengan `jq` atau skrip CI.

## Penyimpanan dan batas hasil

Profil dan laporan berada di `~/.local/share/smtp-test/`; `SMTP_TEST_HOME` mengganti
lokasi tersebut. Profil menggunakan JSON. Penulisan file bersifat atomik dengan
permission 0600. Nama profil sama akan diperbarui. Penghapusan profil tidak
menghapus riwayat diagnosis.

Password, payload AUTH, serta body/attachment tidak disimpan dalam log aplikasi.
Transcript merupakan catatan perintah/respons diagnosis, bukan raw wire dump:
payload AUTH dan DATA dihilangkan. Secret literal dan representasi AUTH PLAIN/LOGIN
base64 disensor dari respons/error yang dicatat. Laporan tetap memuat metadata
konfigurasi, alamat penerima (termasuk Bcc), Message-ID, serta respons server.

Validasi sertifikat selalu aktif. Autentikasi SMTP pada mode Plain ditolak.
IMAP hanya mendukung koneksi terenkripsi. Folder dibuka read-only dan header dibaca
dengan BODY.PEEK sehingga tidak mengubah flag Seen. Maksimal 100 kandidat hasil
SEARCH terakhir diperiksa terhadap Message-ID tepat.

Pesan yang diterima SMTP belum membuktikan penerimaan inbox. DSN merupakan
permintaan kepada server, tanpa jaminan notifikasi akan dikirim. Verifikasi IMAP
membuktikan keberadaan pesan pada folder yang dipilih saat pemeriksaan. Header
SPF/DKIM/DMARC dilaporkan oleh penerima; aplikasi tidak memverifikasi trust header
atau menjalankan evaluator/validator autentikasi pesan penuh.

DNS domain memeriksa record pada domain tepat yang dimasukkan; tidak menelusuri
SPF include/redirect atau fallback DMARC ke organizational domain. Record SPF dan
DMARC tersedia tidak menjamin email lolos autentikasi.

Pemeriksaan relay tanpa DATA hanya menilai penerimaan envelope pada sesi tersebut;
penolakan bisa berasal dari kebijakan lain dan penerimaan RCPT bukan bukti open
relay. Kosongkan username bila ingin mengetes sesi tanpa autentikasi.

Suite dibatasi 20 profil dan maksimal 100 koneksi. Pembatalan kooperatif:
operasi socket aktif menunggu timeout sebelum tahap berikutnya dihentikan.
Resolusi hostname mengikuti resolver OS dan tidak dibatasi timeout socket;
diagnostik record domain menggunakan timeout resolver tersendiri. TCP dapat
mencakup percobaan beberapa alamat. Tidak ada retry pengiriman otomatis. Jika
koneksi terputus saat DATA, status penerimaan bisa belum pasti; periksa mailbox
sebelum mengirim ulang.

OAuth, evaluasi SPF/DKIM/DMARC penuh, dan pengujian beban pengiriman massal berada
di luar cakupan saat ini.

## Pengujian pengembangan

```bash
uv run pytest -q
```

Tes mencakup server SMTP lokal, MIME, envelope, DSN, SMTPUTF8, penolakan penerima,
ukuran pesan, fallback HELO, STARTTLS dan implicit TLS dengan sertifikat nyata,
sertifikat tidak dipercaya, autentikasi, DNS, IMAP read-only, suite, penyimpanan,
CLI, dan interaksi TUI headless. OpenSSL diperlukan untuk membuat sertifikat tes;
tes TLS akan dilewati jika executable tersebut tidak tersedia.

Referensi implementasi: [Textual workers](https://textual.textualize.io/guide/workers/),
[smtplib](https://docs.python.org/3/library/smtplib.html),
[imaplib](https://docs.python.org/3/library/imaplib.html), dan
[dnspython resolver](https://dnspython.readthedocs.io/en/stable/resolver-class.html).
