"""Server-side calls to Supabase with the secret key: invite emails and file storage.

The secret key never leaves the server. Email goes out through the project's own email setup
(Authentication > Emails), so invites look like every other datawazi sign-in email.
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)
BUCKET = "datawazi"


class SupabaseError(Exception):
    pass


class SupabaseAdmin:
    def __init__(self, url: str, secret_key: str, timeout: float = 30):
        self.url = url.rstrip("/")
        self.key = secret_key
        self.timeout = timeout

    def _call(self, method: str, path: str, body=None, data: bytes | None = None, headers: dict | None = None,
              query: dict | None = None) -> tuple[int, bytes]:
        target = f"{self.url}{path}" + (f"?{urllib.parse.urlencode(query)}" if query else "")
        payload = data if data is not None else (json.dumps(body).encode() if body is not None else None)
        all_headers = {"apikey": self.key, "Authorization": f"Bearer {self.key}",
                       "Content-Type": "application/json", **(headers or {})}
        request = urllib.request.Request(target, data=payload, method=method, headers=all_headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except urllib.error.URLError as exc:
            raise SupabaseError(f"Supabase could not be reached: {exc.reason}") from exc

    @staticmethod
    def _message(raw: bytes) -> str:
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return raw[:200].decode(errors="replace")
        return str(body.get("msg") or body.get("message") or body.get("error_description") or body.get("error") or body)

    # Email

    def send_invite(self, email: str, link: str) -> None:
        """Email someone a sign-in that lands on their invite link. New people get an account at the same time."""
        status, raw = self._call("POST", "/auth/v1/invite", {"email": email}, query={"redirect_to": link})
        if status < 300:
            return
        message = self._message(raw)
        if status == 422 or "already" in message.lower():
            # They already have an account: a normal sign-in email, landing on the invite.
            status, raw = self._call("POST", "/auth/v1/otp", {"email": email, "create_user": False},
                                     query={"redirect_to": link})
            if status < 300:
                return
            message = self._message(raw)
        raise SupabaseError(message)

    # Storage: one private bucket, a folder per workspace.

    def ensure_bucket(self) -> None:
        status, raw = self._call("POST", "/storage/v1/bucket", {"id": BUCKET, "name": BUCKET, "public": False})
        if status >= 300 and "already exists" not in self._message(raw).lower() and status != 409:
            raise SupabaseError(f"Could not create the storage bucket: {self._message(raw)}")

    def upload(self, path: str, data: bytes) -> None:
        status, raw = self._call("POST", f"/storage/v1/object/{BUCKET}/{urllib.parse.quote(path)}", data=data,
                                 headers={"Content-Type": "application/octet-stream", "x-upsert": "true"})
        if status >= 300:
            raise SupabaseError(f"Upload failed: {self._message(raw)}")

    def download(self, path: str) -> bytes | None:
        status, raw = self._call("GET", f"/storage/v1/object/{BUCKET}/{urllib.parse.quote(path)}")
        if status == 404 or (status == 400 and "not found" in self._message(raw).lower()):
            return None
        if status >= 300:
            raise SupabaseError(f"Download failed: {self._message(raw)}")
        return raw

    def delete(self, paths: list[str]) -> None:
        if paths:
            self._call("DELETE", f"/storage/v1/object/{BUCKET}", {"prefixes": paths})


class PublishedBackup:
    """Keeps each published dataset version in storage, so dashboards survive a new or rebuilt server."""

    def __init__(self, admin: SupabaseAdmin):
        self.admin = admin

    @staticmethod
    def path(workspace_id: str, dataset_id: str, filename: str) -> str:
        return f"{workspace_id}/datasets/{dataset_id}/{filename}"

    def save(self, workspace_id: str, dataset_id: str, filename: str, data: bytes) -> None:
        try:
            self.admin.upload(self.path(workspace_id, dataset_id, filename), data)
        except SupabaseError:
            log.exception("Backing up %s of dataset %s failed; the local copy still works", filename, dataset_id)

    def restore(self, workspace_id: str, dataset_id: str, filename: str) -> bytes | None:
        try:
            return self.admin.download(self.path(workspace_id, dataset_id, filename))
        except SupabaseError:
            log.exception("Restoring %s of dataset %s failed", filename, dataset_id)
            return None

    def forget(self, workspace_id: str, dataset_id: str, filenames: list[str]) -> None:
        try:
            self.admin.delete([self.path(workspace_id, dataset_id, f) for f in filenames])
        except SupabaseError:
            log.exception("Removing old copies of dataset %s from storage failed", dataset_id)
