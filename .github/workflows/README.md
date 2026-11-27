# BEI Auto Scanner — Final Free (GitHub Actions + Telegram)

## Isi paket
Ekstrak ZIP ini. Unggah **isi folder hasil ekstraksi** ke root repository GitHub, bukan file ZIP-nya. File `scanner.py`, `bot_commands.py`, `config.json`, `requirements.txt`, folder `data/`, dan folder `.github/` harus langsung terlihat di halaman Code repository. Jangan sampai seluruh isi berada di dalam folder pembungkus tambahan.

## GitHub Secrets
Atur di Settings → Secrets and variables → Actions → Repository secrets:
- `TELEGRAM_BOT_TOKEN`: token bot dari BotFather.
- `TELEGRAM_CHAT_ID`: ID chat numerik milik akun/grup yang diizinkan menerima perintah. Jangan unggah token ke repository atau kirim token di chat.

## Menjalankan
1. Ekstrak ZIP. Unggah seluruh isi arsip ke **root** repository `bei-auto-scanner-free`, lalu commit perubahan. Jangan mengunggah ZIP sebagai satu file.
2. Pastikan file `bot_commands.py` terlihat di tab Code dan kedua workflow di `.github/workflows/` sudah terganti. Hapus workflow lama/duplikat yang masih menjalankan `python -u bot_commands.py` dari root atau `git add data/`.
3. Jika log Actions masih persis menampilkan `Run python -u bot_commands.py` atau `git add data/`, berarti workflow lama/duplikat masih dijalankan atau perubahan belum di-commit. Buka nama workflow pada bagian atas log untuk mengidentifikasi file YAML pemicunya.
4. Buka Actions → Telegram Commands → Run workflow.
5. Kirim `/start` ke bot Telegram. Tunggu workflow selesai; jadwal polling berjalan setiap 10 menit pada hari kerja sesuai cron (GitHub Actions dapat menunda jadwal).
6. Uji `/help` dan `/status`. Jalankan `/top10` atau `/scan` setelah bot merespons.
7. Jalankan workflow **BEI Auto Scanner** secara manual untuk menguji pemindaian.

## Perintah Telegram
- `/start` atau `/help` — daftar perintah
- `/status` — status bot
- `/top10` atau `/scan` — pemindaian on-demand
- `/analyze KODE` — analisis satu saham, contoh `/analyze BBCA`
- `/watchlist` — tampilkan watchlist
- `/add KODE` dan `/remove KODE` — kelola watchlist
- `/performance` — ringkasan evaluasi sinyal

## Catatan penting
- Data harga memakai Yahoo Finance melalui yfinance; dapat tertunda, tidak lengkap, dan bukan data real-time/orderbook.
- Universe IDX dapat gagal diakses; dalam kondisi itu scanner memakai fallback terbatas, bukan seluruh saham BEI.
- Fundamental bersifat best-effort dan harus diverifikasi pada laporan resmi emiten.
- GitHub Actions terjadwal dapat terlambat atau tidak berjalan seketika. Perintah Telegram diproses ketika workflow Telegram Commands berjalan.
- Ini alat penyaring informasi, bukan rekomendasi beli/jual atau jaminan keuntungan.
