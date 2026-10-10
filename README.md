# Groceries Agent

Automates adding items to OurGroceries shopping lists via a local Flask API.

## Prerequisites

- Python 3.10+
- pip
- A modern Chromium-based browser installed (Playwright can bundle one if missing)

## Setup

### 1. Clone and enter the project

```bash
git clone <your-repo-url>
cd Groceries-Agent
```

### 2. Create a virtual environment

```bash
python -m venv .venv
```

Activate it:

**Windows (PowerShell):**

```powershell
.venv\Scripts\Activate.ps1
```

**macOS / Linux:**

```bash
source .venv/bin/activate
```

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```



### 4. Install Playwright browsers

This downloads a headless Chromium binary that Playwright uses:

```bash
playwright install chromium
```



### 5. Create your .env file

**Windows (PowerShell):**

```powershell
Copy-Item .env.example .env
```

**macOS / Linux:**

```bash
cp .env.example .env
```

Edit `.env` if you want to change the port or enable debug mode. The defaults work out of the box:

```
PORT=5000
FLASK_ENV=development
```



### 6. Update store URLs (optional)

Open `store_config.py` and replace the placeholder list URL with your actual OurGroceries list URL for each store:

```python
STORE_LISTS: dict[str, str] = {
    "aldi": "https://www.ourgroceries.com/your-lists/list/YOUR-LIST-ID-HERE",
}
```

To get your list URL: go to [ourgroceries.com](https://www.ourgroceries.com), sign in, open the list you want to automate, and copy the URL from the browser address bar.

### 7. Log in to OurGroceries (first-time only)

OurGroceries requires a login session.  Run this once to sign in — it opens a
real browser window, saves your session, and closes:

```bash
python login.py
```

A browser will open to the OurGroceries sign-in page.  Log in with your
email and password.  Once you see your list overview, press **Enter** in the
terminal.  Your session is saved in `.browser-profile/` and reused by the
server on future runs.  If your session expires later, just re-run `python login.py`.

## Running the server

```bash
python app.py
```

You should see:

```
 * Running on http://0.0.0.0:5000
```



## Testing the endpoint

Open a second terminal and run:

**Windows (PowerShell) — success:**

```powershell
Invoke-RestMethod -Method POST -Uri http://localhost:5000/add -ContentType "application/json" -Body '{"store": "aldi", "items": {"eggs": 3, "milk": 2}}'
```

**macOS / Linux — success:**

```bash
curl -X POST http://localhost:5000/add \
  -H "Content-Type: application/json" \
  -d '{"store": "aldi", "items": {"eggs": 3, "milk": 2}}'
```

**Expected success response:**

```json
{"added": ["eggs x3", "milk x2"], "status": "success", "store": "aldi"}
```

**Test invalid store (should return 400):**

PowerShell:

```powershell
Invoke-RestMethod -Method POST -Uri http://localhost:5000/add -ContentType "application/json" -Body '{"store": "nonexistent", "items": {"eggs": 1}}'
```

macOS / Linux:

```bash
curl -X POST http://localhost:5000/add \
  -H "Content-Type: application/json" \
  -d '{"store": "nonexistent", "items": {"eggs": 1}}'
```

Expected:

```json
{"error": "unknown store: nonexistent"}
```

**Test malformed items (should return 400):**

PowerShell:

```powershell
Invoke-RestMethod -Method POST -Uri http://localhost:5000/add -ContentType "application/json" -Body '{"store": "aldi", "items": {"eggs": 0}}'
```

macOS / Linux:

```bash
curl -X POST http://localhost:5000/add \
  -H "Content-Type: application/json" \
  -d '{"store": "aldi", "items": {"eggs": 0}}'
```

Expected:

```json
{"error": "quantity for 'eggs' must be a positive integer, got 0"}
```



## WhatsApp Integration Setup



### Prerequisites

- A Meta Developer account (free)
- ngrok installed with a free static domain



### Setting up ngrok

1. Sign up at ngrok.com (free)
2. Download and install ngrok
3. Add your auth token:

```bash
ngrok config add-authtoken <your-token>
```

1. Claim your one free static domain in the ngrok dashboard under "Domains"
2. Start the tunnel pointing at your Flask server:

```bash
ngrok http --domain=pinchable-waspiest-alma.ngrok-free.dev 5000
```

1. Your server is now publicly reachable at `https://yourname.ngrok-free.app`



### Setting up Meta Developer account and WhatsApp Cloud API

1. Go to [developers.facebook.com](https://developers.facebook.com) and create an account
2. Click "Create App" → choose "Business" → give it a name
3. On the app dashboard, click "Add Product" → find "WhatsApp" → click "Set up"
4. Under "Getting Started" you will see:
  - A **temporary access token** (valid 24 hours for testing)
  - A **test phone number** with its **Phone Number ID**
  - A **test recipient** field — add your own WhatsApp number here so Meta allows you to receive messages during testing
5. Copy the **Phone Number ID** and **access token** into your `.env` file
6. Make up any string for `WA_VERIFY_TOKEN` (e.g. `my-secret-token-123`) and add it to `.env`



### Registering your webhook

1. In the Meta dashboard, go to WhatsApp → Configuration → Webhook
2. Click "Edit" and enter:
  - **Callback URL:** `https://yourname.ngrok-free.app/whatsapp`
  - **Verify token:** the value you set for `WA_VERIFY_TOKEN` in `.env`
3. Click "Verify and Save" — Meta will call your `GET /whatsapp` endpoint to confirm it's reachable
4. After verification, click "Manage" next to Webhook Fields and subscribe to the `**messages`** field



### Testing the WhatsApp flow

Once everything is wired up, send a message from your WhatsApp to the test number Meta gave you. Any natural language grocery request works:

```
Add 3 eggs and 2 milks to Aldi
```

Expected reply:

```
Added: eggs x3, milk x2 to Aldi ✓
```



### Message format

The bot accepts any natural language grocery request. Examples:

```
Add 3 eggs and 2 milks to Aldi
aldi needs eggs, milk, and bread
Can you add toilet paper x3 to Costco
costco: olive oil 1, paper towels 2   ← original format still works
```

A Gemini API key is required for message parsing. Get one free (no credit
card required) at [https://aistudio.google.com](https://aistudio.google.com) — click "Get API Key".
Add it to your .env file as GEMINI_API_KEY.

Free tier limits: 10 requests/minute, 500 requests/day — more than enough
for personal use.

Valid store names match the keys in `store_config.py`.

## Troubleshooting


| Problem                                                       | Fix                                                                                                             |
| ------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `ModuleNotFoundError: No module named 'flask'`                | Your venv isn't active. Run `.venv\Scripts\Activate.ps1` (Windows) or `source .venv/bin/activate` (macOS/Linux) |
| `playwright._impl._api_types.Error: Executable doesn't exist` | Run `playwright install chromium`                                                                               |
| `RuntimeError: No browser profile found`                      | Run `python login.py` to create a login session                                                                 |
| `Login session expired`                                       | Re-run `python login.py` to log in again                                                                        |
| `playwright failed: Locator.fill: Timeout`                    | Login session is missing or expired — run `python login.py`                                                     |
| Server starts but items don't appear on OurGroceries          | Make sure `store_config.py` has your real list URL, not the placeholder                                         |
| `Address already in use`                                      | Another process is on port 5000. Change `PORT` in `.env` or kill the other process                              |
| `curl -X` fails with "parameter cannot be found"              | PowerShell aliases `curl` to `Invoke-WebRequest`. Use `Invoke-RestMethod` (shown above) or `curl.exe` instead   |


