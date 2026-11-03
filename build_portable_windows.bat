@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==== Build Auto Download - FB Photo (portable) ====
if not exist .venv (
  py -m venv .venv || python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install -q --upgrade pip
pip install -q -r requirements.txt pyinstaller
if errorlevel 1 goto err
pyinstaller --noconfirm --clean --onefile --windowed --name "Auto-Download-FB-Photo" --icon "assets\icon.ico" --add-data "assets;assets" --collect-data customtkinter --collect-all playwright auto_download_fb_photo.py
if errorlevel 1 goto err
if not exist Portable mkdir Portable
copy /Y "dist\Auto-Download-FB-Photo.exe" "Portable\" >nul
echo XONG: %cd%\Portable\Auto-Download-FB-Photo.exe
explorer Portable
pause
exit /b 0
:err
echo LOI khi build. Kiem tra da cai Python 3.9+ tu python.org chua.
pause
exit /b 1
