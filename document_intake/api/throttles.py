from rest_framework.throttling import UserRateThrottle


class UploadRateThrottle(UserRateThrottle):
    """Per-user upload limit; rate set by ``DEFAULT_THROTTLE_RATES["intake_upload"]``."""

    scope = "intake_upload"
