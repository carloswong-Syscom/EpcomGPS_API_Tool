@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    set "PYLAUNCHER=py -3"
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set "PYLAUNCHER=python"
    ) else (
        echo No se encontro Python instalado en este equipo.
        echo Instala Python 3 desde https://www.python.org/downloads/ y vuelve a ejecutar este archivo.
        pause
        exit /b 1
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo Creando entorno virtual...
    %PYLAUNCHER% -m venv .venv
    if errorlevel 1 (
        echo No se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
)

echo Instalando dependencias...
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo Fallo la instalacion de dependencias.
    pause
    exit /b 1
)

echo Iniciando Consola Wialon...
start "" ".venv\Scripts\pythonw.exe" "wialon_console.py"
endlocal
