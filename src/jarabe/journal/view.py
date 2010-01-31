# Copyright (C) 2009, Tomeu Vizoso
# Copyright (C) 2010, Aleksey Lim
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

import logging
from gettext import gettext as _
import time

import gobject
import gtk
import hippo

from sugar.graphics import style
from sugar.graphics.icon import CanvasIcon
from sugar.graphics.icon import Icon

from jarabe.journal import model
from jarabe.journal.listview import ListView
from jarabe.journal.thumbsview import ThumbsView


UPDATE_INTERVAL = 300

VIEW_LIST = 0
VIEW_THUMBS = 1
_MESSAGE_PROGRESS = 2
_MESSAGE_EMPTY_JOURNAL = 3
_MESSAGE_NO_MATCH = 4

PAGE_SIZE = 10


class View(gtk.EventBox):

    __gsignals__ = {
        'clear-clicked': (gobject.SIGNAL_RUN_FIRST,
                          gobject.TYPE_NONE,
                          ([])),
        'entry-activated': (gobject.SIGNAL_RUN_FIRST,
                            gobject.TYPE_NONE,
                            ([str])),
    }

    def __init__(self, selection=False):
        gtk.EventBox.__init__(self)

        self._query = {}
        self._result_set = None
        self._pages = {}
        self._current_page = None
        self._view = None
        self._last_progress_bar_pulse = None
        self._progress_bar = None

        self._page_ctors = {
                VIEW_LIST: lambda: self._view_new(ListView, selection),
                VIEW_THUMBS: lambda: self._view_new(ThumbsView, selection),
                _MESSAGE_PROGRESS: self._progress_new,
                _MESSAGE_EMPTY_JOURNAL: self._message_new,
                _MESSAGE_NO_MATCH: self._message_new,
                }

        self.connect('destroy', self.__destroy_cb)

        # Auto-update stuff
        self._fully_obscured = True
        self._dirty = False
        self._update_dates_timer = None

        model.created.connect(self.__model_created_cb)
        model.updated.connect(self.__model_updated_cb)
        model.deleted.connect(self.__model_deleted_cb)

        self.view = VIEW_LIST

    def get_view(self):
        return self._pages[self._view].child

    def set_view(self, view):
        if self._page == self._view:
            # change view only if current page is view as well
            self._page = view
        self._view = view
        self.view.set_result_set(self._result_set)

    view = property(get_view, set_view)

    def _set_page(self, page):
        if self._current_page == page:
            return
        self._current_page = page

        if page in self._pages:
            child = self._pages[page]
        else:
            child = self._page_ctors[page]()
            child.show_all()
            self._pages[page] = child

        if self.child is not None:
            self.remove(self.child)
        self.add(child)

    def _get_page(self):
        return self._current_page

    _page = property(_get_page, _set_page)

    def __model_created_cb(self, signal, **kwargs):
        self._set_dirty(kwargs['mountpoint'])

    def __model_updated_cb(self, signal, **kwargs):
        self._set_dirty(kwargs['mountpoint'])

    def __model_deleted_cb(self, signal, **kwargs):
        self._set_dirty(kwargs['mountpoint'])

    def __destroy_cb(self, widget):
        if self._result_set is not None:
            self._result_set.stop()

    def update_with_query(self, query_dict):
        logging.debug('ListView.update_with_query')
        self._query = query_dict

        if 'order_by' not in self._query:
            self._query['order_by'] = ['+timestamp']

        self.refresh()

    def refresh(self):
        logging.debug('View.refresh query %r', self._query)

        if self._result_set is not None:
            self._result_set.stop()
        self._dirty = False

        self._result_set = model.find(self._query, PAGE_SIZE)
        self._result_set.ready.connect(self.__result_set_ready_cb)
        self._result_set.progress.connect(self.__result_set_progress_cb)
        self._result_set.setup()

    def __result_set_ready_cb(self, **kwargs):
        if self._result_set.length == 0:
            if self._is_query_empty():
                self._page = _MESSAGE_EMPTY_JOURNAL
            else:
                self._page = _MESSAGE_NO_MATCH
        else:
            self._page = self._view
            self.view.set_result_set(self._result_set)

    def _is_query_empty(self):
        # FIXME: This is a hack, we shouldn't have to update this every time
        # a new search term is added.
        if self._query.get('query', '') or self._query.get('mime_type', '') or \
                self._query.get('keep', '') or self._query.get('mtime', '') or \
                self._query.get('activity', ''):
            return False
        else:
            return True

    def __result_set_progress_cb(self, **kwargs):
        if self._page != _MESSAGE_PROGRESS:
            self._last_progress_bar_pulse = time.time()
            self._page = _MESSAGE_PROGRESS

        if time.time() - self._last_progress_bar_pulse > 0.05:
            self._progress_bar.pulse()
            self._last_progress_bar_pulse = time.time()

    def _view_new(self, view_class, selection):
        view = view_class(selection)
        view.modify_bg(gtk.STATE_NORMAL, style.COLOR_WHITE.get_gdk_color())
        view.connect('entry-activated', self.__entry_activated_cb)

        scrolled_view = gtk.ScrolledWindow()
        scrolled_view.set_policy(gtk.POLICY_NEVER, gtk.POLICY_AUTOMATIC)
        scrolled_view.add(view)

        return scrolled_view

    def _progress_new(self):
        alignment = gtk.Alignment(xalign=0.5, yalign=0.5, xscale=0.5)

        self._progress_bar = gtk.ProgressBar()
        self._progress_bar.props.pulse_step = 0.01
        alignment.add(self._progress_bar)

        return alignment

    def _message_new(self):
        canvas = hippo.Canvas()

        box = hippo.CanvasBox(orientation=hippo.ORIENTATION_VERTICAL,
                              background_color=style.COLOR_WHITE.get_int(),
                              yalign=hippo.ALIGNMENT_CENTER,
                              spacing=style.DEFAULT_SPACING,
                              padding_bottom=style.GRID_CELL_SIZE)
        canvas.set_root(box)

        icon = CanvasIcon(size=style.LARGE_ICON_SIZE,
                          icon_name='activity-journal',
                          stroke_color = style.COLOR_BUTTON_GREY.get_svg(),
                          fill_color = style.COLOR_TRANSPARENT.get_svg())
        box.append(icon)

        if self._page == _MESSAGE_EMPTY_JOURNAL:
            text = _('Your Journal is empty')
        elif self._page == _MESSAGE_NO_MATCH:
            text = _('No matching entries')
        else:
            raise ValueError('Invalid message')

        text = hippo.CanvasText(text=text,
                xalign=hippo.ALIGNMENT_CENTER,
                font_desc=style.FONT_BOLD.get_pango_desc(),
                color = style.COLOR_BUTTON_GREY.get_int())
        box.append(text)

        if self._page == _MESSAGE_NO_MATCH:
            button = gtk.Button(label=_('Clear search'))
            button.connect('clicked', self.__clear_button_clicked_cb)
            button.props.image = Icon(icon_name='dialog-cancel',
                                      icon_size=gtk.ICON_SIZE_BUTTON)
            canvas_button = hippo.CanvasWidget(widget=button,
                                               xalign=hippo.ALIGNMENT_CENTER)
            box.append(canvas_button)

        return canvas

    def __clear_button_clicked_cb(self, button):
        self.emit('clear-clicked')

    def update_dates(self):
        self.view.refill()

    def _set_dirty(self, mountpoint):
        if mountpoint not in self._query.get('mountpoints', []):
            return
        if self._fully_obscured:
            self._dirty = True
        else:
            self.refresh()

    def set_is_visible(self, visible):
        if visible != self._fully_obscured:
            return

        logging.debug('canvas_visibility_notify_event_cb %r', visible)
        if visible:
            self._fully_obscured = False
            if self._dirty:
                self.refresh()
            if self._update_dates_timer is None:
                logging.debug('Adding date updating timer')
                self._update_dates_timer = \
                        gobject.timeout_add_seconds(UPDATE_INTERVAL,
                                            self.__update_dates_timer_cb)
        else:
            self._fully_obscured = True
            if self._update_dates_timer is not None:
                logging.debug('Remove date updating timer')
                gobject.source_remove(self._update_dates_timer)
                self._update_dates_timer = None

    def __update_dates_timer_cb(self):
        self.update_dates()
        return True

    def __entry_activated_cb(self, sender, uid):
        self.emit('entry-activated', uid)
