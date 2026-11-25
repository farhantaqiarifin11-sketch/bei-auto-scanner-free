# BEI Auto Scanner Free Edition

Scanner teknikal BEI berbasis Python + GitHub Actions + Telegram. Didesain agar dapat dipasang dari HP melalui browser.

## Fitur
- Mengambil daftar emiten dari endpoint profil perusahaan BEI.
- Mengambil OHLCV intraday 5 menit dari Yahoo Finance melalui `yfinance`.
- Menghitung MA5/20/50, RSI, VWAP sesi, relative volume, breakout/pullback/momentum.
- Mengirim kandidat berskor minimal 70 ke Telegram.
- Jadwal otomatis setiap 20 menit pada hari kerja, sekitar jam bursa WIB.

## Batasan penting
1. Data Yahoo Finance bukan feed resmi BEI, tidak dijamin real-time, dapat tertunda, kosong, dibatasi, atau berubah format.
2. Endpoint daftar emiten BEI dapat berubah atau menolak request otomatis. Jika gagal, scanner berhenti kecuali `tickers_fallback.txt` diisi dengan daftar lengkap yang diperbarui.
3. Semua ticker yang berhasil didapat belum tentu memiliki data intraday di Yahoo Finance. Laporan mencatat jumlah data yang berhasil.
4. GitHub Actions tidak menjamin eksekusi tepat pada menit cron; jadwal dapat terlambat/terlewat.
5. Indikator MA dihitung dari candle 5-menit. Entry/TP/SL adalah level indikatif berbasis range, bukan rekomendasi yang tervalidasi order book.
6. Sistem ini tidak mempunyai order book, running trade, broker flow, ataupun tick-by-tick gratis.
7. Jangan gunakan uang sungguhan sebelum paper trading dan memeriksa data/harga langsung di aplikasi sekuritas.

## Instalasi hanya dengan HP

### A. Buat Telegram Bot
1. Buka Telegram, cari `@BotFather`.
2. Kirim `/newbot`, ikuti instruksi, simpan token bot.
3. Buka bot yang dibuat lalu tekan Start.
4. Untuk mengetahui chat ID, buka `https://api.telegram.org/botTOKEN/getUpdates` di browser setelah mengirim pesan ke bot. Ganti `TOKEN` dengan token milik sendiri. Jangan bagikan token ke siapa pun.
5. Catat angka `chat.id`.

### B. Buat repository GitHub
1. Buka https://github.com dan buat akun/masuk.
2. Tekan `+` → New repository.
3. Nama: `bei-auto-scanner-free`.
4. Pilih **Public** agar penggunaan GitHub-hosted runner standar dapat gratis; pahami bahwa kode repository akan terlihat publik. Token tetap disimpan di Secrets, jangan pernah dimasukkan ke file.
5. Buat repository.

### C. Unggah file proyek
Unggah semua file dan folder dari ZIP ini ke root repository, dengan struktur:
```
.github/workflows/scanner.yml
data/                       (folder boleh kosong)
config.json
requirements.txt
scanner.py
tickers_fallback.txt
README_ID.md
```
Di GitHub mobile web, gunakan `Add file` → `Upload files`. Untuk folder `.github/workflows`, buat folder melalui web editor dengan membuat file `.github/workflows/scanner.yml`, lalu tempel isi file workflow dari ZIP.

### D. Simpan rahasia Telegram
1. Repository → Settings → Secrets and variables → Actions.
2. New repository secret:
   - `TELEGRAM_BOT_TOKEN` = token dari BotFather.
   - `TELEGRAM_CHAT_ID` = chat ID Telegram.
3. Jangan taruh token di `config.json`, source code, atau chat publik.

### E. Jalankan tes
1. Buka tab Actions.
2. Pilih `BEI Auto Scanner`.
3. Tekan `Run workflow`.
4. Buka log run. Pastikan daftar emiten dan data berhasil diambil.
5. Periksa Telegram.

## Konfigurasi
Edit `config.json`:
- `min_score`: ambang skor (default 70).
- `min_avg_value_idr`: filter likuiditas rata-rata nilai transaksi per candle; sesuaikan setelah memeriksa satuan data Yahoo.
- `max_alerts`: jumlah maksimum kandidat per laporan.
- `batch_size`: jumlah ticker per permintaan batch; jika sering gagal, turunkan ke 15–20.
- `max_tickers`: maksimum jumlah ticker.

## Perhatian tentang daftar ticker
Jika endpoint BEI ditolak, isi `tickers_fallback.txt` dengan daftar kode saham yang benar-benar lengkap dan aktif (satu kode per baris). Daftar contoh bawaan hanya 20 ticker dan TIDAK mewakili seluruh BEI. Jangan menyebut scanner full-market bila fallback tersebut belum dilengkapi.

## Catatan GitHub Actions
Cron menggunakan UTC:
- `0,20,40 2-8 * * 1-5` = 09:00 sampai 15:40 WIB.
- `0 9 * * 1-5` = 16:00 WIB.
GitHub dapat menunda atau melewatkan jadwal saat beban tinggi. Repository publik dapat menonaktifkan scheduled workflow jika tidak ada aktivitas selama 60 hari; cek Actions secara berkala.

## Keamanan
- Jangan pernah commit Telegram bot token.
- Bila token terlanjur terlihat, revoke melalui BotFather dan buat token baru.
- Sistem hanya memberi sinyal; tidak melakukan transaksi otomatis.
