# Copyright (C) 2015-2016 Sam Parkinson
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA
	
import os
import json
import socket
import logging
from gettext import gettext as _

from gi.repository import GObject
from gi.repository import Gtk
from gi.repository import Gio
from gi.repository import GLib
import dbus

import telepathy.interfaces as tpinterfaces
import telepathy.constants as tpconstants
from telepathy.client import Connection, Channel

from sugar3.graphics.icon import Icon
from sugar3.presence import presenceservice
from sugar3.presence.connectionmanager import get_connection_manager
from sugar3.activity.activity import SCOPE_PRIVATE
from sugar3.graphics.alert import NotifyAlert, Alert
from sugar3.datastore import datastore

from jarabe.journal import journalwindow


PROJECT_BUNDLE_ID = 'org.sugarlabs.Project'
# WTF is this interface?  Is isn't in the spec and I can't find docs for
# it anywhere.  Non standard stuff from OLPC days I guess
CONN_INTERFACE_ACTIVITY_PROPERTIES = 'org.laptop.Telepathy.ActivityProperties'
CONN_INTERFACE_BUDDY_INFO = 'org.laptop.Telepathy.BuddyInfo'


def _ensure_channel(function):
    def __channel_made_cb(channel, user_data):
        self, args, kwargs = user_data
        channel.disconnect_by_func(__channel_made_cb)
        function(self, *args, **kwargs)

    def wrap(self, *args, **kwargs):
        if self._text_channel.props.ready:
            function(*args, **kwargs)
        else:
            self._text_channel.make_chan()
            self._text_channel.channel_made.connect(
                __channel_made_cb, (self, args, kwargs))
    return wrap


class ProjectCollab(GObject.GObject):

    def __init__(self, activity_id, object_id=None):
        GObject.GObject.__init__(self)
        self._id = activity_id
        self.object_id = object_id
        conn = get_connection_manager().get_preferred_connection()
        self._text_channel = _LazyTextChannel(self._id, conn)

    def take_handle(self, handle, channel_path):
        '''
        Take a handle, eg found from a ChannelDispatcher notification.

        Args:
            handle (int): room handle
            channel_path (str): dbus path to the channel object
        '''
        self._text_channel.take_handle(handle, channel_path)
        def hello(chan):
            chan.post('G\'day')
        self._text_channel.channel_made.connect(hello)

    @_ensure_channel
    def invite(self, buddies):
        """
        Invite the given buddy to join this project.  If the project does not
        yet have a channel, this call will be silently delayed while
        the channel is created.

        Args:
            buddies (list of jarabe.model.buddy.BuddyModel):  buddies to invite
        """
        self._update_metadata()
        self._text_channel.chan.AddMembers(
            [buddy.props.handle for buddy in buddies],
            'please-join-my-project',
            dbus_interface=tpinterfaces.CHANNEL_INTERFACE_GROUP,
            reply_handler=self.__invite_cb,
            error_handler=self.__invite_cb)

    def __invite_cb(self, *args):
        return

    def _update_metadata(self):
        if self.object_id is None:
            raise Exception('Can not update metadata without object id')
        jobject = datastore.get(self.object_id)
        metadata = jobject.metadata.get_dictionary()
        self._text_channel.set_metadata(metadata)


_projects = {}

def get_project_collab(activity_id, object_id=None):
    '''
    Get the project collab instance for a given project, will create a new
    project collab instance if needed

    Args:
        activity_id (str): activity_id of the project (from jobject metadata)
        object_id (str): will be used in constructing the ProjectCollab

    Returns: a ProjectCollab
    '''
    global _projects
    if activity_id not in _projects:
        print('Making project %s collab' % activity_id)
        # FIXME:  Search if we need to join an existing tp text channel
        _projects[activity_id] = ProjectCollab(activity_id,
                                               object_id=object_id)
    return _projects[activity_id]


class _LazyTextChannel(GObject.GObject):
    '''
    Lazy create telepathy text channel.

    Args:
        id (str): an id that is unique (eg. activity_id)
        connection: whatever the connection manager returns - a tuple

    Attributes:
        chan: Telepathy text channel, or None by default
    '''

    channel_made = GObject.Signal('channel-made')

    def __init__(self, id, connection):
        GObject.GObject.__init__(self)
        # Some idiot passed it as a dbus.ByteArray
        self._id = str(id)
        connection_path, self._connection = connection
        self.chan = None
        self._making_channel = False

    @GObject.Property
    def ready(self):
        return self.chan is not None

    def make_chan(self):
        if self._making_channel:
            return

        self._making_channel = True
        self._connection.RequestHandles(
            tpconstants.HANDLE_TYPE_ROOM,
            [self._id],
            reply_handler=self.__got_handles_cb,
            error_handler=self.__error_handler_cb,
            dbus_interface=tpinterfaces.CONNECTION)

    def __got_handles_cb(self, handles):
        self._room_handle = handles[0]
        self._connection.RequestChannel(
            tpinterfaces.CHANNEL_TYPE_TEXT,
            tpconstants.HANDLE_TYPE_ROOM,
            self._room_handle, True,
            reply_handler=self.__create_text_channel_cb,
            error_handler=self.__error_handler_cb,
            dbus_interface=tpinterfaces.CONNECTION)

    def take_handle(self, handle, channel_path):
        self._room_handle = handle
        self.__create_text_channel_cb(channel_path)

    def __create_text_channel_cb(self, channel_path):
        Channel(self._connection.requested_bus_name, channel_path,
                ready_handler=self.__text_channel_ready_cb)

    def __text_channel_ready_cb(self, channel):
        self.chan = channel
        self._connection.AddActivity(
            self._id,
            self._room_handle,
            reply_handler=self.__added_activity_cb,
            error_handler=self.__error_handler_cb,
            dbus_interface=CONN_INTERFACE_BUDDY_INFO)

    def __added_activity_cb(self):
        self._making_channel = False
        self.channel_made.emit()

    def set_metadata(self, metadata):
        self._connection.SetProperties(
            self._room_handle,
            {'name': json.dumps(metadata),
             'type': PROJECT_BUNDLE_ID},
            dbus_interface=CONN_INTERFACE_ACTIVITY_PROPERTIES)
        self.chan[tpinterfaces.CHANNEL_TYPE_TEXT].connect_to_signal(
            'Received', self.__received)

    def __error_handler_cb(self, error):
        print('Lazy create channel error', error)
        self._making_channel = False

    def post(self, data):
        self.chan[tpinterfaces.CHANNEL_TYPE_TEXT].Send(
            tpconstants.CHANNEL_TEXT_MESSAGE_TYPE_NORMAL,
            json.dumps(data))

    def __received(self, identity, timestamp, sender, type_, flags, text):
        print('GOT A MESSAGE', identity, timestamp, sender, type_, flags, text)
