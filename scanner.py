
name: BEI Auto scanner

on:
  schedule:
    # Top 10 pukul 06.00 WIB, Senin-Jumat
    - cron: "0 23 * * 0-4"

    # Pemindaian terakhir sebelum pembukaan, 08.40 WIB
    - cron: "40 1 * * 1-5"

    # Pemindaian rutin setiap 20 menit, 09.00-15.40 WIB
    - cron: "0,20,40 2-8 * * 1-5"

    # Notifikasi momentum pukul 09.15 WIB
    - cron: "15 2 * * 1-5"

  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: bei-auto-scanner
  cancel-in-progress: false

jobs:
  scan:
    runs-on: ubuntu-latest
    timeout-minutes: 15

    steps:
      - name: Ambil kode scanner
        uses: actions/checkout@v4

      - name: Siapkan Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Instal kebutuhan
        run: pip install -r requirements.txt

      - name: Jalankan scanner
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          TZ: Asia/Jakarta
        run: python scanner.py

      - name: Simpan riwayat alert
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add -A data/
          git diff --cached --quiet || git commit -m "Update scanner alert state"
          git push
