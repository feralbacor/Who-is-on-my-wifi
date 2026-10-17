# Home Network Monitor

See who is on your home network, live, from a small dashboard on your Windows PC.
Get a Telegram message when a device you have never seen joins your Wi-Fi.

- **Overview**: live topology map, alert summary, Wi-Fi band and signal health, recent activity
- **Assets**: every device ever seen, with vendor, IP, MAC, band and signal. Rename devices and mark the ones that are yours
- **Alerts**: unknown devices online, unknown devices with a weak signal (possibly outside your home), scanner problems
- **Activity log**: who connected and disconnected, and when
- **Reports**: activity over the last 24 h or 7 days, most active devices, CSV export
- **Settings**: status of the scanner, router integration and Telegram alerts

No dependencies: plain Python standard library and one HTML file.

## Requirements

- Windows 10 or 11 (the scanner uses PowerShell)
- Python 3.9 or newer, with `pythonw` on your `PATH`
- Your PC connected to the home network you want to watch

## Quick start

1. Download or clone this repository.
2. Double-click `start.bat`. The dashboard opens at <http://localhost:8765>.
3. The first scan takes about 10 seconds. Click **Mark as mine** on every device you recognise.

That's it. Telegram alerts and the router integration below are optional.

To start it with Windows, put a shortcut to `start.bat` (or to `pythonw monitor.py`) in
`shell:startup` (press Win+R and type `shell:startup`). Starting it twice is safe: the second copy exits on its own.

## Configuration (optional)

Copy `config.example.json` to `config.json` and fill in what you want to use:

```json
{
  "telegram_token": "",
  "telegram_chat_id": "",
  "router_user": "",
  "router_password": ""
}
```

`config.json` is in `.gitignore`. Never commit it.

### Telegram alerts

You get a message the first time an unknown device joins your network.

1. In Telegram, talk to **@BotFather**, send `/newbot` and copy the token it gives you.
2. Send any message to your new bot.
3. Open `https://api.telegram.org/bot<token>/getUpdates` in a browser and copy the number in `"chat":{"id": ...}`.
4. Put the token and that number in `config.json`. No restart needed.

### Wi-Fi band and signal (ZTE F680 routers only)

With your router's admin login in `router_user` and `router_password`, the monitor reads the router's
"Associated Devices" pages every minute. You then see which devices are on 2.4 GHz or 5 GHz, their signal
in dBm, and phones that are asleep (and don't answer pings) stay marked online.

It only works on ZTE F680 routers. On other routers leave these fields empty; everything else works the same.
While the monitor is logged in (about a second every minute) the router may log you out of its own web page.

## What to change

Everything lives in two files:

| What | Where |
| --- | --- |
| Dashboard port (default `8765`) | `PUERTO` in `monitor.py` |
| Pause between scans (default 10 s) | `PAUSA` in `monitor.py` |
| Missed scans before a device counts as disconnected | `FALLOS_PARA_DESCONECTAR` in `monitor.py` |
| Router pages for each Wi-Fi band | `PAGINAS_BANDA` in `monitor.py` |
| Telegram message text | `avisar_telegram()` in `monitor.py` |
| Alert rules and severities | `alerts()` in `index.html` |
| Look and colours | the `:root` variables at the top of `index.html` |

Internal names in the code are in Spanish (it started as a personal project). The dashboard and this README are in English.

## How it works

- `escanear.ps1` pings every address in your PC's /24 subnet, waits for Windows to resolve them, and returns
  the devices in the ARP table as JSON.
- `monitor.py` runs that scan in a loop, keeps the device list and event log in `dispositivos.json`,
  looks up vendors from the MAC prefix, sends Telegram alerts and serves the dashboard.
- `index.html` is the whole dashboard. It asks `/api/estado` for fresh data every 3 seconds.

Run the self-check after changing `monitor.py`:

```
python monitor.py --test
```

## Privacy and security

- The dashboard only answers on `localhost`, and rejects requests that come through other host names.
  Other devices on your network can't open it.
- Your device list stays in `dispositivos.json` on your PC. It is in `.gitignore`.
- Only the first half of each MAC address (the vendor prefix) is sent to `api.macvendors.com`.
- Phones and tablets often use a random ("private") MAC per network. They show up with a **Private MAC** tag.

## Limits

- It sees which devices are on your network, not what they send. It can't inspect traffic or see attacks from the internet.
- It only scans a /24 subnet (for example `192.168.1.0` to `192.168.1.255`).
- It doesn't run in Docker on Windows: Docker Desktop's virtual machine can't see the MAC addresses on your home network.
