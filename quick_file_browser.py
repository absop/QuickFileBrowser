import os
import re
import threading
import webbrowser

from contextlib import contextmanager
from os.path import isfile, isdir
from os.path import abspath, relpath

import sublime
import sublime_plugin


class Debug:
    __slots__ = []

    _debug = False

    @classmethod
    def print(cls, *args):
        if not cls._debug:
            return
        print(f'{__package__}:', *args)

    @classmethod
    def set_debug(cls, debug):
        cls._debug = debug
        state = ['closing', 'opening'][debug]
        print(f'{__package__}: debug is {state}')


error = sublime.error_message
is_windows = sublime.platform() == 'windows'


class SideBarQuickFileBrowserCommand(sublime_plugin.WindowCommand):
    def is_visible(self, paths, **args):
        return len(paths) == 1 and os.path.exists(paths[0])

    def run(self, paths, **args):
        QuickPanelFileBrowser(self.window, paths[0], **args)


class WindowQuickFileBrowserCommand(sublime_plugin.WindowCommand):
    def is_enabled(self, **args):
        view = self.window.active_view()
        if path := view.file_name():
            return os.path.exists(path)
        return False

    def is_visible(self, **args):
        return self.is_enabled(**args)

    def run(self, **args):
        view = self.window.active_view()
        QuickPanelFileBrowser(self.window, view.file_name(), **args)


class PathInputHandler(sublime_plugin.TextInputHandler):
    def __init__(self, is_wanted=os.path.exists, path_type='Existing Path'):
        self.is_wanted = is_wanted
        self.path_type = path_type

    def placeholder(self):
        return self.path_type

    def initial_text(self):
        view = sublime.active_window().active_view()
        if view:
            path = view.file_name()
            if path and os.path.exists(path):
                return path
        return ''

    def validate(self, path):
        return self.is_wanted(path)


class WindowQuickFileBrowserInputPathCommand(sublime_plugin.WindowCommand):
    def run(self, path, **args):
        QuickPanelFileBrowser(self.window, path, **args)

    def input(self, *args, **kwargs):
        return PathInputHandler()


class QuickFileBrowserSavePathCommand(sublime_plugin.WindowCommand):
    def run(self, path):
        QuickPanelFileBrowser.path_list.get(self.window.id(), []).append(path)
        sublime.status_message(f'Saved {path}')


class QuickFileBrowserOpenFileCommand(sublime_plugin.WindowCommand):
    def run(self, path, open_in_sublime):
        if open_in_sublime:
            self.window.open_file(path)
            sublime.status_message('Opened ' + path)
        else:
            webbrowser.open_new_tab(path)


KIND_FILE = (sublime.KIND_ID_VARIABLE, 'F', "File")
KIND_DIRECTORY = (sublime.KIND_ID_VARIABLE, 'D', "Directory")


class QuickPanelFileBrowser:
    path_list = {}
    separator = 4 * '&nbsp;'
    FLAGS = ( sublime.KEEP_OPEN_ON_FOCUS_LOST
            | sublime.WANT_EVENT
            | sublime.MONOSPACE_FONT
            )

    def __init__(self, window, path, recursive=False):
        if isfile(path):
            path = os.path.dirname(path)
        elif not isdir(path):
            sublime.error_message(
                f'{__package__}: No such file or directory:\n'
                f'    {path}')
            return
        self.window = window
        self.init_path = path
        self.path_list[window.id()] = []

        if not recursive:
            self.browse(path)
        else:
            task = StatusBarTask(lambda: self.list_files(path),
                'Listing files...', 'Done')
            StatusBarThread(task, window)

    def list_files(self, path):
        Debug.print(f'list_files {path}')
        paths = []
        items = []
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not self.exclude_folder(d)]
            for file in files:
                if self.exclude_file(file):
                    continue
                ext = os.path.splitext(file)[1]
                if ext in self.ignored_file_types:
                    continue
                if ext not in self.file_type_icons:
                    ext = '.*'

                absolute = normalize_path(os.path.join(root, file))
                relative = normalize_path(relpath(absolute, self.init_path))
                actions = self.action_tags(absolute, relative, ext)
                annotation = self.file_type_icons[ext].name

                paths.append(absolute)
                items.append(
                    sublime.QuickPanelItem(
                        file,
                        details=[relative, self.separator.join(actions)],
                        annotation=annotation,
                        kind=KIND_FILE
                    )
                )
        Debug.print(f'items: {len(items)}')
        self.show_quick_panel(paths, items, '')

    def browse(self, curdir):
        Debug.print(f'browse {curdir}')
        pardir = normalize_path(abspath(os.path.join(curdir, '..')))
        curdir = normalize_path(curdir)
        paths = [pardir, curdir]
        items = [
            self.make_item('..', pardir, None, KIND_DIRECTORY),
            self.make_item(curdir, curdir, None, KIND_DIRECTORY)
        ]
        items[0].annotation = 'parent ' + items[0].annotation
        items[1].annotation = 'current ' + items[1].annotation
        for entry in os.listdir(curdir):
            if entry[0] == '.' and not self.show_hidden_files:
                continue
            absolute = join_path(curdir, entry)
            if isfile(absolute):
                ext = os.path.splitext(entry)[1]
                if ext in self.ignored_file_types:
                    continue
                if ext not in self.file_type_icons:
                    ext = '.*'
                kind = KIND_FILE
            else:
                ext = None
                kind = KIND_DIRECTORY
            paths.append(absolute)
            items.append(self.make_item(entry, absolute, ext, kind))
        Debug.print(f'items: {len(items)}')
        self.show_quick_panel(paths, items, curdir)

    def make_item(self, entry, absolute, ext, kind):
        relative = normalize_path(relpath(absolute, self.init_path))
        actions = self.action_tags(absolute, relative, ext)
        annotation = self.file_type_icons[ext].name
        return sublime.QuickPanelItem(
                    entry,
                    details=self.separator.join(actions),
                    annotation=annotation,
                    kind=kind
                )

    def action_tags(self, absolute, relative, ext):
        icon = self.file_type_icons[ext].icon
        url = sublime.command_url(
            'quick_file_browser_open_file',
            args={
                'path': absolute,
                'open_in_sublime': ext == '.*'
            }
        )

        def save_path_url(path):
            return sublime.command_url(
                'quick_file_browser_save_path',
                args={'path': path}
            )

        def insert_path_url(path):
            return sublime.command_url(
                'insert',
                args={'characters': path}
            )

        def make_tags(operation, make_url):
            return f"""\
<em>{operation} Path</em>:{2 * '&nbsp;'}\
<a href="{make_url(absolute)}">absolute</a>,{2 * '&nbsp;'}\
<a href="{make_url(relative)}">relative</a>;"""

        return [
            make_tags('Copy', save_path_url),
            make_tags('Insert', insert_path_url),
            f'<a href="{url}">{icon}</a>'
        ]

    def show_quick_panel(self, paths, items, curdir):
        def on_done(index, event):
            if index == -1:
                if path_list := self.path_list.pop(self.window.id()):
                    sublime.set_clipboard('\n'.join(path_list))
                    sublime.status_message(f'Copied {len(path_list)} paths')
                return

            path = paths[index]
            item = items[index]
            if item.kind == KIND_FILE:
                self.window.open_file(path)
                if event.get('modifier_keys', {}).get('primary', False):
                    show(items, on_done, flags=self.FLAGS)
            else:
                if path != curdir:
                    try:
                        self.browse(path)
                    except Exception as e:
                        sublime.status_message(str(e))
                        show(items, on_done, flags=self.FLAGS)
                else:
                    show(items, on_done, flags=self.FLAGS)

        show = self.window.show_quick_panel
        show(items, on_done, flags=self.FLAGS)

    @classmethod
    def initialize(cls):
        global join_path, normalize_path

        Debug.set_debug(settings.get('debug', False))

        if is_windows and settings.get('use_unix_style_path', True):
            join_path = lambda path, leaf: '/'.join([path, leaf])
            normalize_path = lambda path: path.replace('\\', '/')
        else:
            join_path = os.path.join
            normalize_path = lambda path: path

        file_exclude_patterns = settings.get('file_exclude_patterns', [])
        folder_exclude_patterns = settings.get('folder_exclude_patterns', [])

        cls.exclude_file = pat2regex(file_exclude_patterns).match
        cls.exclude_folder = pat2regex(folder_exclude_patterns).match
        cls.show_hidden_files = settings.get('show_hidden_files', True)
        cls.ignored_file_types = settings.get('ignored_file_types', [])
        cls.file_type_icons = file_type_icons = {}

        for name, file_type in settings.get('file_types', {}).items():
            icon = file_type.get('icon', 'Open')
            file_type_icon = FileTypeIcon(name, icon)
            extensions = file_type.get('extensions', [])
            if isinstance(extensions, list):
                for extension in extensions:
                    file_type_icons[extension] = file_type_icon
            else:
                file_type_icons[extensions] = file_type_icon

        if '.*' not in file_type_icons:
            file_type_icons['.*'] = FileTypeIcon('file', 'Open')
        if None not in file_type_icons:
            file_type_icons[None] = FileTypeIcon('folder', 'Open')


class FileTypeIcon:
    __slots__ = ['name', 'icon']

    def __init__(self, name, icon):
        self.name = name
        self.icon = icon

    def __repr__(self):
        return f'FileTypeIcon(name="{name}", icon="{icon}")'



class StatusBarTask:
    def __init__(self, function, message, success):
        self.function = function
        self.message = message
        self.success = success

    def attach(self, status_bar):
        self.status_bar = status_bar

    def status_message(self):
        return f'{self.message} {self.status_bar.status}'

    def finish_message(self):
        return self.success


class StatusBarThread:
    def __init__(self, task, window, key='__z{|}~__'):
        self.state = 7
        self.step = 1
        self.last_view = None
        self.need_refresh = True
        self.window = window
        self.key = key
        self.status = ''
        self.task = task
        self.task.attach(self)
        self.thread = threading.Thread(target=task.function)
        self.thread.start()
        self.update_status_message()

    @contextmanager
    def pause(self):
        self.need_refresh = False
        yield
        self.need_refresh = True

    def update_status_message(self):
        self.update_status_bar()
        if self.need_refresh:
            self.show_status_message(self.task.status_message())
        if not self.thread.is_alive():
            cleanup = self.last_view.erase_status
            self.last_view.set_status(self.key, self.task.finish_message())
            sublime.set_timeout(lambda: cleanup(self.key), 2000)
        else:
            sublime.set_timeout(self.update_status_message, 100)

    def update_status_bar(self):
        if self.state == 0 or self.state == 7:
            self.step = -self.step
        self.state += self.step
        self.status = f"[{' ' * self.state}={' ' * (7 - self.state)}]"

    def show_status_message(self, message):
        active_view = self.window.active_view()
        active_view.set_status(self.key, message)
        if self.last_view != active_view:
            self.last_view and self.last_view.erase_status(self.key)
            self.last_view = active_view



def pat2regex(patterns):
    transtable = str.maketrans({
        '*': '.*',
        '.': '\\.',
        '?': '.'
    })

    def convert(pattern):
        return str.translate(pattern, transtable)

    try:
        regex = re.compile(f'^(?:{"|".join(map(convert, patterns))})$')
    except:
        regex = re.compile('.*')
        sublime.error_message(f'Invalid patterns: {patterns}')

    return regex


def plugin_loaded():
    global settings
    settings = sublime.load_settings(f'{__package__}.sublime-settings')
    settings.add_on_change('file_types', QuickPanelFileBrowser.initialize)
    QuickPanelFileBrowser.initialize()


def plugin_unloaded():
    settings.clear_on_change('file_types')
