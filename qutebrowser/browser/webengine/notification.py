# vim: ft=python fileencoding=utf-8 sts=4 sw=4 et:

# Copyright 2016-2020 Florian Bruhin (The Compiler) <mail@qutebrowser.org>
#
# This file is part of qutebrowser.
#
# qutebrowser is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# qutebrowser is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with qutebrowser.  If not, see <http://www.gnu.org/licenses/>.

"""Handles sending notifications over DBus."""

from typing import Dict, Any

from qutebrowser.utils import log

from PyQt5.QtGui import QImage
from PyQt5.QtCore import QVariant, QMetaType, QByteArray
from PyQt5.QtDBus import QDBusConnection, QDBusInterface, QDBus, QDBusArgument
from PyQt5.QtWebEngineCore import QWebEngineNotification
from PyQt5.QtWebEngineWidgets import QWebEngineProfile


class DBusException(Exception):
    """Raised when something goes wrong with talking to DBus."""


class DBusNotificationManager:
    """Manages notifications that are sent over DBus."""

    def __init__(self):
        # Dict mapping notification IDs to the corresponding QWebEngine
        # notification objects.
        self.active_notifications = {}
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            raise DBusException("Failed to connect to DBus session bus")

        self.interface = QDBusInterface(
            "org.freedesktop.Notifications",  # service
            "/org/freedesktop/Notifications",  # path
            "org.freedesktop.Notifications",  # interface
            bus,
        )

        if not self.interface:
            raise DBusException("Could not construct a DBus interface")

    def set_as_presenter_for(self, profile: QWebEngineProfile) -> None:
        """Sets the profile to use the manager as the presenter."""
        # PyQtWebEngine unrefs the callback after it's called, for some reason.
        # So we call setNotificationPresenter again to *increase* its refcount
        # to prevent it from getting GC'd. Otherwise, random methods start
        # getting called with the notification as `self`, or segfaults happen,
        # or other badness.
        def _present_and_reset(qt_notification: QWebEngineNotification) -> None:
            profile.setNotificationPresenter(_present_and_reset)
            self._present(qt_notification)

        profile.setNotificationPresenter(_present_and_reset)

    def _present(self, qt_notification: QWebEngineNotification) -> None:
        """Shows a notification over DBus.

        This should *not* be directly passed to setNotificationPresenter
        because of a bug in the PyQtWebEngine bindings.
        """
        # notification id 0 means 'assign us the ID'. We can't just pass 0
        # because it won't get sent as the right type.
        notification_id = QVariant(0)
        notification_id.convert(QVariant.UInt)

        actions_list = QDBusArgument([], QMetaType.QStringList)

        qt_notification.show()
        hints: Dict[str, Any] = {
            # Include the origin in case the user wants to do different things
            # with different origin's notifications.
            "x-qutebrowser-origin": qt_notification.origin().toDisplayString()
        }
        if not qt_notification.icon().isNull():
            hints["image-data"] = self._convert_image(qt_notification.icon())

        reply = self.interface.call(
            QDBus.BlockWithGui,
            "Notify",
            "qutebrowser",  # application name
            notification_id,
            "qutebrowser",  # icon
            qt_notification.title(),
            qt_notification.message(),
            actions_list,
            hints,
            -1,  # timeout; -1 means 'use default'
        )

        if not (len(reply.arguments()) == 1):
            raise DBusException(
                "Got an unexpected number of reply arguments {}".format(
                    len(reply.arguments())
                )
            )

        notification_id = reply.arguments()[0]
        log.sessions.debug("Sent out notification {}".format(notification_id))

    def _convert_image(self, qimage: QImage) -> QDBusArgument:
        """Converts a QImage to the structure DBus expects."""
        # This is apparently what GTK-based notification daemons expect; tested it with dunst.
        # Otherwise you get weird color schemes.
        qimage.convertTo(QImage.Format_RGBA8888)
        image_data = QDBusArgument()
        image_data.beginStructure()
        image_data.add(qimage.width())
        image_data.add(qimage.height())
        image_data.add(qimage.bytesPerLine())
        image_data.add(qimage.hasAlphaChannel())
        # RGBA_8888 always has 8 bits per color, 4 channels.
        image_data.add(8)
        image_data.add(4)
        # sizeInBytes() is preferred, but PyQt complains it's a private method.
        bits = qimage.constBits().asstring(qimage.byteCount())
        image_data.add(QByteArray(bits))
        image_data.endStructure()
        return image_data
