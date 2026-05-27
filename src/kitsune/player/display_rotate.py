# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import logging

from gi.repository import Gio, GLib

log = logging.getLogger('kitsune.player.display_rotate')

_BUS_NAME = 'org.gnome.Mutter.DisplayConfig'
_OBJ_PATH = '/org/gnome/Mutter/DisplayConfig'
_IFACE = 'org.gnome.Mutter.DisplayConfig'

_PROXY_FLAGS = (Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES
                | Gio.DBusProxyFlags.DO_NOT_CONNECT_SIGNALS)


def check_available(callback):
    """Async check whether Mutter DisplayConfig D-Bus service exists.

    Calls callback(True) if available, callback(False) otherwise.
    """
    def _on_proxy(source, result):
        try:
            proxy = Gio.DBusProxy.new_for_bus_finish(result)
            owner = proxy.get_name_owner()
            callback(owner is not None)
        except Exception:
            callback(False)

    Gio.DBusProxy.new_for_bus(
        Gio.BusType.SESSION,
        _PROXY_FLAGS | Gio.DBusProxyFlags.DO_NOT_AUTO_START,
        None,
        _BUS_NAME,
        _OBJ_PATH,
        _IFACE,
        None,
        _on_proxy,
    )


class DisplayRotator:
    """Toggle display rotation via org.gnome.Mutter.DisplayConfig."""

    def __init__(self):
        self._proxy = None
        self._serial = 0
        self._baseline_transform = 0
        self._rotated = False
        self._ready = False
        self._init_proxy()

    def _init_proxy(self):
        def _on_proxy(source, result):
            try:
                self._proxy = Gio.DBusProxy.new_for_bus_finish(result)
                self._fetch_state()
            except Exception as e:
                log.warning('DisplayConfig proxy failed: %s', e)

        Gio.DBusProxy.new_for_bus(
            Gio.BusType.SESSION,
            _PROXY_FLAGS,
            None,
            _BUS_NAME,
            _OBJ_PATH,
            _IFACE,
            None,
            _on_proxy,
        )

    def _fetch_state(self):
        self._proxy.call(
            'GetCurrentState',
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            self._on_state,
        )

    def _on_state(self, proxy, result):
        try:
            res = proxy.call_finish(result)
            self._serial = res.get_child_value(0).get_uint32()
            self._baseline_transform = self._parse_transform(res)
            self._ready = True
            log.debug('display state: serial=%d baseline_transform=%d',
                      self._serial, self._baseline_transform)
        except Exception as e:
            log.warning('GetCurrentState failed: %s', e)

    @staticmethod
    def _parse_transform(res):
        """Extract current transform from first logical monitor."""
        logical = res.get_child_value(2)
        if logical.n_children() > 0:
            first = logical.get_child_value(0)
            return first.get_child_value(3).get_uint32()
        return 0

    @staticmethod
    def _parse_current_mode(res, connector):
        """Find current mode_id for a connector from the monitors array.

        GetCurrentState returns monitors as:
          a((ssss) spec, a(siiddada{sv}) modes, a{sv} props)
        where spec is (connector, vendor, product, serial_str)
        and each mode has a{sv} props with 'is-current' boolean.
        """
        monitors = res.get_child_value(1)
        for i in range(monitors.n_children()):
            mon = monitors.get_child_value(i)
            spec = mon.get_child_value(0)
            mon_connector = spec.get_child_value(0).get_string()
            if mon_connector != connector:
                continue
            modes = mon.get_child_value(1)
            for j in range(modes.n_children()):
                mode = modes.get_child_value(j)
                mode_id = mode.get_child_value(0).get_string()
                props = mode.get_child_value(6)
                is_current = False
                for k in range(props.n_children()):
                    entry = props.get_child_value(k)
                    key = entry.get_child_value(0).get_string()
                    if key == 'is-current':
                        is_current = entry.get_child_value(1).get_variant().get_boolean()
                        break
                if is_current:
                    return mode_id
        return None

    @staticmethod
    def _parse_logical_monitors(res):
        """Parse logical monitors from GetCurrentState for building Apply params.

        Returns list of (x, y, scale, transform, primary, [(connector, ...)])
        """
        logical = res.get_child_value(2)
        result = []
        for i in range(logical.n_children()):
            lm = logical.get_child_value(i)
            x = lm.get_child_value(0).get_int32()
            y = lm.get_child_value(1).get_int32()
            scale = lm.get_child_value(2).get_double()
            transform = lm.get_child_value(3).get_uint32()
            primary = lm.get_child_value(4).get_boolean()
            mons = lm.get_child_value(5)
            connectors = []
            for j in range(mons.n_children()):
                m = mons.get_child_value(j)
                connectors.append(m.get_child_value(0).get_string())
            result.append((x, y, scale, transform, primary, connectors))
        return result

    def _apply_config(self, target_transform, callback=None):
        if not self._proxy or not self._ready:
            log.warning('DisplayConfig not ready')
            if callback:
                callback(False)
            return

        def _on_fresh_state(proxy, result):
            try:
                res = proxy.call_finish(result)
                serial = res.get_child_value(0).get_uint32()
                logical = self._parse_logical_monitors(res)

                if not logical:
                    log.warning('no logical monitors')
                    if callback:
                        callback(False)
                    return

                # Build logical monitors for ApplyMonitorsConfig
                apply_logical = []
                for x, y, scale, transform, primary, connectors in logical:
                    # Use target_transform only for the first logical monitor
                    t = target_transform if not apply_logical else transform
                    monitor_specs = []
                    for conn in connectors:
                        mode_id = self._parse_current_mode(res, conn)
                        if mode_id:
                            monitor_specs.append(
                                GLib.Variant('(ssa{sv})', (conn, mode_id, {}))
                            )
                    if not monitor_specs:
                        log.warning('no current mode for connectors %s', connectors)
                        if callback:
                            callback(False)
                        return
                    apply_logical.append(
                        GLib.Variant('(iiduba(ssa{sv}))',
                                     (x, y, scale, t, primary, monitor_specs))
                    )

                params = GLib.Variant(
                    '(uua(iiduba(ssa{sv}))a{sv})',
                    (serial, 1, apply_logical, {}),
                )

                self._proxy.call(
                    'ApplyMonitorsConfig',
                    params,
                    Gio.DBusCallFlags.NONE,
                    -1,
                    None,
                    self._on_applied,
                    callback,
                )
            except Exception as e:
                log.warning('apply config failed: %s', e)
                if callback:
                    callback(False)

        self._proxy.call(
            'GetCurrentState',
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            _on_fresh_state,
        )

    def _on_applied(self, proxy, result, callback=None):
        try:
            proxy.call_finish(result)
            log.debug('ApplyMonitorsConfig succeeded')
            if callback:
                callback(True)
        except Exception as e:
            log.warning('ApplyMonitorsConfig failed: %s', e)
            if callback:
                callback(False)

    def toggle(self, callback=None):
        """Toggle between baseline and 90° rotation."""
        if self._rotated:
            self.restore(callback)
        else:
            self._rotate_90(callback)

    def _rotate_90(self, callback=None):
        def _on_done(success):
            if success:
                self._rotated = True
            if callback:
                callback(success)

        if self._baseline_transform == 1:
            target = 0
        else:
            target = 1

        self._apply_config(target, _on_done)

    def restore(self, callback=None):
        def _on_done(success):
            if success:
                self._rotated = False
            if callback:
                callback(success)

        self._apply_config(self._baseline_transform, _on_done)

    @property
    def is_rotated(self):
        return self._rotated
