"""
Auth helpers for `browser_use.qa`.

The pipeline accepts a Playwright-format `storage_state.json` path because
that is what `AuthScanSession` consumes. These helpers cover the common
ways to GET that file without driving a headful browser yourself:

  - `write_storage_from_cookies` — for "I already have a session cookie"
  - `write_storage_from_localstorage` — for SPAs that read a bearer/auth
    token from localStorage instead of a cookie
  - `capture_via_form_login` — script the email+password form yourself,
    headless or headful; works for any traditional auth flow

For the magic-link / OAuth / 2FA case, keep using `AuthCaptureSession`
directly — the human handles the weird flow once, the state lives on
disk forever after.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from urllib.parse import urlparse

from browser_use.integrations.webmap import AuthCaptureSession

logger = logging.getLogger(__name__)


def write_storage_from_cookies(
	storage_state_path: Path,
	base_url: str,
	cookies: dict[str, str],
	*,
	domain: str | None = None,
	path: str = '/',
	secure: bool = False,
	http_only: bool = False,
	same_site: str = 'Lax',
) -> Path:
	"""Write a Playwright storage_state JSON containing the given cookies.

	Each `cookies` entry becomes one Playwright cookie record scoped to
	`domain` (defaults to `base_url`'s host). Use this when you already
	have a session cookie from another channel (CI secret, dev console,
	service-account token endpoint) and want to skip the UI login.

	Returns the same path passed in, for chaining.
	"""
	if domain is None:
		domain = urlparse(base_url).hostname or 'localhost'
	storage_state_path.parent.mkdir(parents=True, exist_ok=True)
	state = {
		'cookies': [
			{
				'name': name,
				'value': value,
				'domain': domain,
				'path': path,
				'expires': -1,
				'httpOnly': http_only,
				'secure': secure,
				'sameSite': same_site,
			}
			for name, value in cookies.items()
		],
		'origins': [],
	}
	storage_state_path.write_text(json.dumps(state, indent=2))
	logger.info(f'🔐 wrote {len(cookies)} cookies to {storage_state_path} (domain={domain})')
	return storage_state_path


def write_storage_from_localstorage(
	storage_state_path: Path,
	base_url: str,
	local_storage: dict[str, str],
	*,
	cookies: dict[str, str] | None = None,
) -> Path:
	"""Write a Playwright storage_state JSON with origin-scoped localStorage.

	Useful for SPAs that read a bearer token from `localStorage` (common
	in React/Vue/Angular apps that gate `fetch` calls with
	`Authorization: Bearer <token>`). Pass the keys the SPA reads —
	typical names: `auth_token`, `accessToken`, `id_token`, `jwt`.

	`cookies` is the same map shape as `write_storage_from_cookies` if
	the SPA additionally sets a CSRF or session cookie alongside the
	token.

	Returns the same path passed in.
	"""
	origin = f'{urlparse(base_url).scheme}://{urlparse(base_url).netloc}'
	storage_state_path.parent.mkdir(parents=True, exist_ok=True)
	cookie_path = write_storage_from_cookies(
		storage_state_path,
		base_url,
		cookies or {},
	)
	state = json.loads(cookie_path.read_text())
	state['origins'] = [
		{
			'origin': origin,
			'localStorage': [{'name': k, 'value': v} for k, v in local_storage.items()],
		}
	]
	storage_state_path.write_text(json.dumps(state, indent=2))
	logger.info(f'🔐 wrote {len(local_storage)} localStorage entries for {origin} to {storage_state_path}')
	return storage_state_path


async def capture_via_form_login(
	storage_state_path: Path,
	*,
	login_url: str,
	fields: dict[str, str],
	submit_selector: str,
	wait_after_submit_sec: float = 4.0,
	headless: bool = True,
) -> Path:
	"""Script a traditional email+password form fill, save the resulting state.

	Args:
	    storage_state_path: where to write the storage_state JSON. Driven
	        by `AuthCaptureSession` so the StorageStateWatchdog handles
	        the save on session stop.
	    login_url: absolute URL of the login form.
	    fields: map of CSS selectors → values to type into them
	        (e.g. `{'#email': 'qa@x.com', '#password': '...'}`).
	    submit_selector: CSS selector for the submit button.
	    wait_after_submit_sec: seconds to wait after submit so the
	        post-login redirect lands and the auth cookie is set.
	    headless: default True because form-fill is fully scripted; flip
	        to False to watch the flow when debugging.

	Returns the storage_state path.
	"""
	async with AuthCaptureSession(storage_state_path, headless=headless) as session:
		await session._cdp_navigate(login_url)
		# Give the page a moment to render before typing.
		await asyncio.sleep(2.0)
		cdp = await session.get_or_create_cdp_session(target_id=None, focus=False)
		sid = cdp.session_id
		await cdp.cdp_client.send.Runtime.enable(session_id=sid)
		for selector, value in fields.items():
			await cdp.cdp_client.send.Runtime.evaluate(
				params={
					'expression': (
						f'(() => {{ const el = document.querySelector({json.dumps(selector)}); '
						f'if (!el) return false; '
						f'const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set; '
						f'setter.call(el, {json.dumps(value)}); '
						f'el.dispatchEvent(new Event("input", {{ bubbles: true }})); '
						f'el.dispatchEvent(new Event("change", {{ bubbles: true }})); '
						f'return true; }})()'
					),
					'returnByValue': True,
				},
				session_id=sid,
			)
		await cdp.cdp_client.send.Runtime.evaluate(
			params={
				'expression': (
					f'(() => {{ const el = document.querySelector({json.dumps(submit_selector)}); '
					f'if (!el) return false; el.click(); return true; }})()'
				),
				'returnByValue': True,
			},
			session_id=sid,
		)
		await asyncio.sleep(wait_after_submit_sec)
		await cdp.cdp_client.send.Runtime.disable(session_id=sid)
	return storage_state_path
