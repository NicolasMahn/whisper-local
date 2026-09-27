"""Exports the app's state on D-Bus for the top-bar extension."""

from gi.repository import Gio, GLib

OBJECT_PATH = "/io/github/nicolasmahn/WhisperLocal"
INTERFACE = "io.github.nicolasmahn.WhisperLocal"
_XML = f"""
<node>
  <interface name="{INTERFACE}">
    <property name="State" type="s" access="read"/>
    <property name="Enabled" type="b" access="readwrite"/>
    <property name="Key" type="s" access="read"/>
    <property name="HandsFreeKey" type="s" access="read"/>
    <property name="Recent" type="as" access="read"/>
    <property name="Problem" type="s" access="read"/>
    <signal name="Level"><arg name="level" type="d"/></signal>
  </interface>
</node>
"""
# D-Bus property name -> (GObject property on the application, variant type)
_PROPERTIES = {
    "State": ("state", "s"),
    "Enabled": ("enabled", "b"),
    "Key": ("key", "s"),
    "HandsFreeKey": ("hands-free-key", "s"),
    "Recent": ("recent", "as"),
    "Problem": ("problem", "s"),
}


class Service:
    """Mirrors the application's GObject properties as D-Bus properties."""

    def __init__(self, application, connection: Gio.DBusConnection):
        self._application = application
        self._connection = connection
        interface = Gio.DBusNodeInfo.new_for_xml(_XML).interfaces[0]
        self._registration = connection.register_object(
            OBJECT_PATH, interface, None, self._get, self._set
        )
        self._notify = application.connect("notify", self._changed)

    def close(self) -> None:
        self._application.disconnect(self._notify)
        self._connection.unregister_object(self._registration)

    def emit_level(self, level: float) -> None:
        self._connection.emit_signal(
            None, OBJECT_PATH, INTERFACE, "Level", GLib.Variant("(d)", (level,))
        )

    def _variant(self, name: str) -> GLib.Variant:
        prop, kind = _PROPERTIES[name]
        value = self._application.get_property(prop)
        return GLib.Variant(kind, list(value) if name == "Recent" else value)

    def _get(self, _connection, _sender, _path, _interface, name):
        return self._variant(name)

    def _set(self, _connection, _sender, _path, _interface, name, value):
        # Only Enabled is writable; D-Bus rejects the others from the XML.
        # GObject notifies on every write, and a signal should mean a change.
        if self._application.props.enabled != value.unpack():
            self._application.props.enabled = value.unpack()
        return True

    def _changed(self, _application, pspec) -> None:
        name = next(
            (name for name, (prop, _) in _PROPERTIES.items() if prop == pspec.name), None
        )
        if name is None:
            return
        self._connection.emit_signal(
            None,
            OBJECT_PATH,
            "org.freedesktop.DBus.Properties",
            "PropertiesChanged",
            GLib.Variant("(sa{sv}as)", (INTERFACE, {name: self._variant(name)}, [])),
        )
