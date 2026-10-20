# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""b14 (EXPERIMENTAL) — integration-registered named triggers for VW Group
Connect vehicles (HA 2026.7+ named-trigger platform).

⚠️ The developer API for this platform is upstream-flagged "still in very active
development and should not be used yet by integrations. The API may change
without a deprecation notice" — and it DID change between HA 2026.6 and dev
(``async_attach_runner`` gained a ``did_not_trigger`` param). So this is opt-in /
experimental, modeled on ``homeassistant/components/sun/trigger.py``, and its
signatures must be re-verified at each HA bump. If the platform is absent on an
older core, ``async_get_triggers`` simply registers nothing.

Each trigger fires when the vehicle state edge is detected on a REAL data update
(coordinator._transition_detector) — no new API calls, purely derived from state
we already poll. v1 fires for ANY of the account's vehicles and hands the action
a payload with the ``vin`` (filter with ``{{ trigger.vin }}`` for one car);
per-device targeting is a future enhancement.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.const import CONF_DEVICE_ID
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType

from ._vol import vol

from .const import DOMAIN
from .trigger_detect import EVENT_KEYS

_LOGGER = logging.getLogger(__name__)

# ``CONF_OPTIONS`` only exists in homeassistant.const on newer cores; the
# key itself is stable ("options"), so define it locally (CI runs older HA).
CONF_OPTIONS = "options"

# v4.7.11 (trigger-vin-targeting) — optional per-vehicle scoping. Our own
# trigger.py docstring flagged "per-device targeting is a future enhancement"; on
# a multi-car account v1 fired for EVERY vehicle, forcing a {{ trigger.vin }}
# template filter in the action. This adds an OPTIONAL ``vin`` option (and the
# standard ``target:`` device selector) so the platform subscribes only for the
# wanted VIN(s). Unset ⇒ v1 account-wide behaviour, byte-identical.
#
# ``vin`` lives under ``options`` and ``device_id`` under the generic ``target``
# because that is how HA splits a named-trigger's config (see
# homeassistant.helpers.trigger.TriggerConfig: key/target/options). VINs are 17
# uppercase alphanumerics (ISO 3779); the coordinator stores them upper-cased, so
# we ``vol.Upper``-normalise the input to match. ``extra=ALLOW_EXTRA`` keeps the
# schema additive if a future option is added.
CONF_VIN = "vin"
_VIN = vol.All(cv.string, vol.Upper, vol.Match(r"^[A-Z0-9]{17}$"))
_OPTIONS_SCHEMA = vol.Schema({vol.Optional(CONF_VIN): _VIN}, extra=vol.ALLOW_EXTRA)


def _resolve_target_vins(hass: HomeAssistant, config: Any) -> set[str] | None:
    """VINs this trigger/condition config is scoped to.

    Three outcomes, and the difference between the last two is the whole point:

    * ``None`` — **nothing was asked for**. Account-wide, the byte-identical v1
      behaviour.
    * a non-empty set — scope to exactly those VINs.
    * an **empty set** — a scope *was* asked for and resolved to no vehicle.
      Match nothing.

    This used to return a bare ``set()`` for all three, and both callers then
    failed OPEN: the trigger subscribed with ``vin=None`` (which the detector
    fires for every VIN) and the condition's ``if vins and …`` guard was skipped
    on a falsy empty set. So an automation written for ONE car silently applied
    to every car on the account — a stale ``device_id`` after a car was removed
    and re-added is enough to land there, and so is a target expressed with any
    field other than ``device_id``. A scoped config that cannot be resolved must
    do nothing, not everything.

    Reads ``options.vin`` and resolves each ``target.device_id`` to its VIN via
    the device registry (vehicle devices carry ``identifiers={(DOMAIN, vin)}``;
    the per-entry settings device uses ``<entry_id>_settings`` — skipped).
    Defensive against the experimental config shape: attributes are read with
    ``getattr``, and a config carrying neither key still means "nothing asked
    for" rather than "asked and failed".
    """
    options = getattr(config, "options", None) or {}
    target = getattr(config, "target", None) or {}
    vin = options.get(CONF_VIN)
    device_ids = target.get(CONF_DEVICE_ID) or []  # cv.TARGET_FIELDS ⇒ a list
    # Anything in ``target`` is a scoping request, including a shape we cannot
    # read — a ``target:`` with an area or a label resolves to no device here,
    # and widening to the whole account would be the same failure in a new coat.
    scoped = bool(vin) or bool(target)
    if not scoped:
        return None

    vins: set[str] = set()
    if vin:
        vins.add(str(vin))
    if device_ids:
        registry = dr.async_get(hass)
        for device_id in device_ids:
            device = registry.async_get(device_id)
            if device is None:
                _LOGGER.warning(
                    "A VW Group Connect trigger or condition targets device %s, "
                    "which no longer exists. It will not match any vehicle until "
                    "the automation is pointed at a current device.",
                    device_id,
                )
                continue
            for domain, ident in device.identifiers:
                if domain == DOMAIN and not str(ident).endswith("_settings"):
                    vins.add(str(ident))
    if not vins:
        _LOGGER.warning(
            "A VW Group Connect trigger or condition is scoped to a target that "
            "resolves to no vehicle (%s). It will not match anything — this used "
            "to silently apply to every vehicle on the account.",
            ", ".join(sorted(target)) or "no usable field",
        )
    return vins

# The named-trigger platform only exists on HA 2026.7+ (and is upstream-flagged
# "may change without a deprecation notice"). Import it defensively so the module
# stays importable — and static-analysable against an older HA baseline — on
# cores that don't have it yet; there, async_get_triggers simply registers
# nothing.
if TYPE_CHECKING:
    from homeassistant.helpers.trigger import (  # type: ignore[attr-defined]
        Trigger,
        TriggerActionRunner,
    )
else:
    try:
        from homeassistant.helpers.trigger import Trigger, TriggerActionRunner
    except ImportError:  # HA < 2026.7 — platform not available
        Trigger = object
        TriggerActionRunner = object


def _coordinators(hass: HomeAssistant) -> list[Any]:
    """Every loaded vag_connect coordinator (one per brand/account entry)."""
    out: list[Any] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        coord = getattr(entry, "runtime_data", None)
        if coord is not None and hasattr(coord, "register_transition_listener"):
            out.append(coord)
    return out


class _VagVehicleTrigger(Trigger):
    """Base for a single vehicle state-transition trigger.

    Subclasses set ``_event_key`` to one of :data:`trigger_detect.EVENT_KEYS`.
    """

    _event_key: str = ""

    @classmethod
    async def async_validate_config(
        cls, hass: HomeAssistant, config: ConfigType
    ) -> ConfigType:
        # config is the {options, target} subset; target is already TARGET_FIELDS-
        # validated by the base. v4.7.11: validate/normalise the optional vin.
        options = config.get(CONF_OPTIONS)
        if options:
            return {**config, CONF_OPTIONS: _OPTIONS_SCHEMA(options)}
        return config

    def __init__(self, hass: HomeAssistant, config: Any) -> None:
        super().__init__(hass, config)
        self._hass = hass
        # v4.7.11 — the base Trigger.__init__ stores only _hass; keep the
        # TriggerConfig (key/target/options) so async_attach_runner can scope by VIN.
        self._config = config

    async def async_attach_runner(
        self,
        run_action: TriggerActionRunner,
        did_not_trigger: Any = None,
    ) -> CALLBACK_TYPE:
        # did_not_trigger is accepted for forward-compat with newer HA cores that
        # pass it (HA 2026.6 does not); we don't use it.
        event_key = self._event_key

        def _on_transition(payload: dict[str, Any]) -> None:
            run_action(payload, f"vehicle {payload.get('event', event_key)}")

        # v4.7.11 (trigger-vin-targeting) — when the config scopes to one or more
        # VINs, subscribe once per VIN (the detector filters per edge); unset ⇒
        # register with vin=None, the byte-identical v1 account-wide behaviour.
        target_vins = _resolve_target_vins(self._hass, self._config)
        # None ⇒ nothing was asked for ⇒ account-wide (vin=None, which
        # trigger_detect fires for every VIN). An EMPTY set means a scope WAS
        # asked for and resolved to no vehicle: subscribe to nothing rather than
        # falling back to the account-wide listener, which is what silently
        # turned a one-car automation into an every-car one.
        vins: tuple[str | None, ...]
        if target_vins is None:
            vins = (None,)
        else:
            vins = tuple(target_vins)
        unsubs = [
            coord.register_transition_listener(event_key, vin, _on_transition)
            for coord in _coordinators(self._hass)
            for vin in vins
        ]

        @callback
        def _remove() -> None:
            for unsub in unsubs:
                unsub()

        return _remove


def _make_trigger(event_key: str) -> type[Trigger]:
    """Build a concrete Trigger subclass for one event key."""

    class _T(_VagVehicleTrigger):
        _event_key = event_key

    _T.__name__ = f"VagVehicle_{event_key}_Trigger"
    _T.__qualname__ = _T.__name__
    return _T


# Registered trigger names → classes. Keys are the stable snake_case ids the
# strings.json "triggers" section + triggers.yaml describe.
TRIGGERS: dict[str, type[Trigger]] = {
    event_key: _make_trigger(event_key) for event_key in sorted(EVENT_KEYS)
}


async def async_get_triggers(hass: HomeAssistant) -> dict[str, type[Trigger]]:
    """HA named-trigger platform hook — expose our vehicle triggers.

    Robust against the experimental API changing: only classes that are actually
    CONCRETE (all abstractmethods implemented) are registered, so if a future HA
    adds/renames a required method, we degrade to registering nothing instead of
    handing HA a class it cannot instantiate.
    """
    return {
        key: cls
        for key, cls in TRIGGERS.items()
        if not getattr(cls, "__abstractmethods__", frozenset())
    }
