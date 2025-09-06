import unittest
from pathlib import Path
from typing import Callable
from unittest.mock import patch, Mock

from ..lib.conversion import ConversionWorker
from ..lib.ebook import Ebook


module_name = 'calibre_plugins.ebook_translator.lib.conversion'


class TestConversionWorker(unittest.TestCase):
    def setUp(self):
        self.gui = Mock()
        self.icon = Mock()
        self.worker = ConversionWorker(self.gui, self.icon)
        self.worker.db = Mock()
        self.worker.api = Mock()

        self.ebook = Mock(Ebook)
        self.job = Mock()
        self.worker.working_jobs = {
            self.job: (self.ebook, str(Path('/path/to/test.epub')))}

    def test_create_worker(self):
        self.assertIsInstance(self.worker, ConversionWorker)

    def test_translate_done_job_failed_debug(self):
        self.job.failed = True
        with patch(module_name + '.DEBUG', True):
            self.worker.translate_done(self.job)
            self.gui.job_exception.assert_not_called()

    def test_translate_done_job_failed_not_debug(self):
        with patch(module_name + '.DEBUG', False):
            self.worker.translate_done(self.job)
            self.gui.job_exception.assert_called_once_with(
                self.job, dialog_title='Translation job failed')

    @patch(module_name + '.os')
    @patch(module_name + '.open')
    @patch(module_name + '.get_metadata')
    @patch(module_name + '.set_metadata')
    def test_translate_done_ebook_to_library(
            self, mock_set_metadata, mock_get_metadata, mock_open, mock_os):
        self.job.failed = False
        self.job.description = 'test description'
        self.job.log_path = '/path/to/log'
        metadata_config = {
            'subjects': ['test subject 1', 'test subject 2'],
            'lang_code': True,
            'lang_mark': True,
        }
        self.worker.config = {
            'ebook_metadata': metadata_config,
            'to_library': True,
        }
        self.ebook.is_extra_format.return_value = False
        self.ebook.title = 'test title'
        self.ebook.input_format = 'epub'
        self.ebook.output_format = 'epub'
        self.ebook.custom_title = 'test custom title'
        self.ebook.target_lang = 'German'
        self.ebook.lang_code = 'de'
        file = Mock()
        mock_open.return_value.__enter__.return_value = file
        metadata = Mock()
        metadata.title = 'test title'
        metadata.tags = []
        metadata.language = 'en'
        mock_get_metadata.return_value = metadata

        self.worker.db.create_book_entry.return_value = 89
        self.worker.api.format_abspath.return_value = '/path/to/test[m].epub'

        self.worker.translate_done(self.job)

        mock_open.assert_called_once_with(
            str(Path('/path/to/test.epub')), 'r+b')
        mock_get_metadata.assert_called_once_with(file, 'epub')
        mock_set_metadata.assert_called_once_with(file, metadata, 'epub')
        self.assertEqual('test custom title [German]', metadata.title)
        self.assertEqual('de', metadata.language)
        self.assertEqual([
            'test subject 1', 'test subject 2', 'Translated by Ebook '
            'Translator: https://translator.bookfere.com'], metadata.tags)

        self.worker.db.create_book_entry.assert_called_once_with(metadata)
        self.worker.api.add_format.assert_called_once_with(
            89, 'epub', str(Path('/path/to/test.epub')), run_hooks=False)
        self.worker.gui.library_view.model.assert_called_once()
        self.worker.gui.library_view.model().books_added \
            .assert_called_once_with(1)
        self.worker.api.format_abspath.assert_called_once_with(89, 'epub')

        self.worker.gui.status_bar.show_message.assert_called_once_with(
            'test description completed', 5000)
        arguments = self.worker.gui.proceed_question.mock_calls[0].args
        self.assertIsInstance(arguments[0], Callable)
        self.assertIs(self.worker.gui.job_manager.launch_gui_app, arguments[1])
        self.assertEqual('/path/to/log', arguments[2])
        self.assertEqual('Ebook Translation Log', arguments[3])
        self.assertEqual('Translation Completed', arguments[4])
        self.assertEqual(
            'The translation of "test custom title [German]" was completed. '
            'Do you want to open the book?',
            arguments[5])

        mock_payload = Mock()
        arguments[0](mock_payload)
        mock_payload.assert_called_once_with(
            'ebook-viewer',
            kwargs={'args': ['ebook-viewer', '/path/to/test[m].epub']})

        arguments = self.worker.gui.proceed_question.mock_calls[0].kwargs
        self.assertEqual(True, arguments.get('log_is_file'))
        self.assertIs(self.icon, arguments.get('icon'))

class TestLangCodeRegression(unittest.TestCase):
    @patch(module_name + '.get_element_handler')
    @patch(module_name + '.get_translation')
    @patch(module_name + '.get_translator')
    def test_export_with_mixed_engine_labels(
            self, mock_get_translator, mock_get_translation,
            mock_get_element_handler):
        from pathlib import Path
        from ..engines.base import Base
        from ..lib.conversion import convert_item
        from ..lib.element import get_element_handler as real_get_element_handler

        captured = {}

        def fake_get_element_handler(placeholder, separator, direction):
            handler = real_get_element_handler(placeholder, separator, direction)
            original = handler.set_translation_lang

            def wrapped(lang):
                captured['lang'] = lang
                original(lang)

            handler.set_translation_lang = wrapped
            return handler

        mock_get_element_handler.side_effect = fake_get_element_handler

        translator = Mock()
        translator.name = 'engineB'
        translator.placeholder = Base.placeholder
        translator.separator = Base.separator
        translator.concurrency_limit = 1
        translator.request_interval = 0
        translator.request_attempt = 1
        translator.request_timeout = 1
        translator.merge_enabled = False
        translator.set_source_lang = Mock()
        translator.set_target_lang = Mock()
        translator.get_iso639_target_code = Mock(return_value='de-DE')
        mock_get_translator.return_value = translator

        class DummyTranslation:
            def set_batch(self, batch):
                pass

            def set_callback(self, cb):
                self.cb = cb

            def set_progress(self, progress):
                pass

            def handle(self, paragraphs):
                data = [
                    ('Hallo', 'engineA', 'de'),
                    ('Welt', 'engineB', 'de-DE'),
                ]
                for p, info in zip(paragraphs, data):
                    text, engine_name, lang = info
                    p.translation = text
                    p.engine_name = engine_name
                    p.target_lang = lang
                    self.cb(p)

        mock_get_translation.return_value = DummyTranslation()

        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            input_path = tmpdir / 'sample.srt'
            output_path = tmpdir / 'out.srt'
            input_path.write_text(
                '1\n00:00:00,000 --> 00:00:01,000\nHello\n\n'
                '2\n00:00:01,000 --> 00:00:02,000\nWorld\n')

            convert_item(
                'title', str(input_path), str(output_path), 'English',
                'German', False, False, 'srt', 'utf-8', 'auto', 'de',
                lambda *a, **k: None)

            self.assertTrue(output_path.exists())
            content = output_path.read_text()
            self.assertIn('Hallo', content)
            self.assertIn('Welt', content)
            self.assertEqual('de', captured.get('lang'))


    @patch(module_name + '.open')
    @patch(module_name + '.open_path')
    @patch(module_name + '.os.rename')
    @patch(module_name + '.get_metadata')
    @patch(module_name + '.set_metadata')
    def test_translate_done_ebook_to_path(
            self, mock_set_metadata, mock_get_metadata, mock_os_rename,
            mock_open_path, mock_open):
        self.job.failed = False
        self.job.description = 'test description'
        self.job.log_path = str(Path('/path/to/log'))
        metadata_config = {
            'subjects': ['test subject 1', 'test subject 2'],
            'lang_code': True,
            'lang_mark': True,
        }
        self.worker.config = {
            'ebook_metadata': metadata_config,
            'to_library': False,
        }
        self.ebook.is_extra_format.return_value = False
        self.ebook.title = 'test title'
        self.ebook.input_format = 'epub'
        self.ebook.output_format = 'epub'
        self.ebook.custom_title = 'test: custom title*'
        self.ebook.target_lang = 'German'
        self.ebook.lang_code = 'de'
        file = Mock()
        mock_open.return_value.__enter__.return_value = file
        metadata = Mock()
        metadata.title = 'test title'
        metadata.tags = []
        metadata.language = 'en'
        mock_get_metadata.return_value = metadata

        self.worker.translate_done(self.job)

        original_path = str(Path('/path/to/test.epub'))
        new_path = str(Path('/path/to/test_ custom title_ [German].epub'))

        mock_open.assert_called_once_with(original_path, 'r+b')
        mock_os_rename.assert_called_once_with(original_path, new_path)
        self.worker.gui.status_bar.show_message.assert_called_once_with(
            'test description ' + 'completed', 5000)
        arguments = self.worker.gui.proceed_question.mock_calls[0].args
        self.assertIsInstance(arguments[0], Callable)
        self.assertIs(self.worker.gui.job_manager.launch_gui_app, arguments[1])
        self.assertEqual(str(Path('/path/to/log')), arguments[2])
        self.assertEqual('Ebook Translation Log', arguments[3])
        self.assertEqual('Translation Completed', arguments[4])
        self.assertEqual(
            'The translation of "test: custom title* [German]" was completed. '
            'Do you want to open the book?',
            arguments[5])

        mock_payload = Mock()
        arguments[0](mock_payload)
        mock_payload.assert_called_once_with(
            'ebook-viewer', kwargs={'args': [
                'ebook-viewer',
                str(Path('/path/to/test_ custom title_ [German].epub'))]})

        arguments = self.worker.gui.proceed_question.mock_calls[0].kwargs
        self.assertEqual(True, arguments.get('log_is_file'))
        self.assertIs(self.icon, arguments.get('icon'))


    @patch(module_name + '.open_path')
    @patch(module_name + '.os.rename')
    @patch(module_name + '.open')
    def test_translate_done_other_to_library(
            self, mock_open, mock_os_rename, mock_open_path):
        self.job.failed = False
        self.job.description = 'test description'
        self.job.log_path = str(Path('/path/to/log'))
        metadata_config = {'lang_mark': True}
        self.worker.config = {
            'ebook_metadata': metadata_config,
            'to_library': True,
        }
        self.ebook.is_extra_format.return_value = True
        self.ebook.id = 89
        self.ebook.title = 'test title'
        self.ebook.custom_title = 'test custom title'
        self.ebook.input_format = 'srt'
        self.ebook.output_format = 'srt'
        self.ebook.custom_title = 'test custom title'
        self.ebook.target_lang = 'German'
        self.worker.working_jobs = {
            self.job: (self.ebook, str(Path('/path/to/test.srt')))}
        metadata = Mock()
        self.worker.api.get_metadata.return_value = metadata
        self.worker.api.format_abspath.return_value = \
            str(Path('/path/to/test[m].srt'))
        self.worker.db.create_book_entry.return_value = 90

        self.worker.translate_done(self.job)

        self.worker.api.get_metadata.assert_called_once_with(89)
        self.worker.db.create_book_entry.assert_called_once_with(metadata)
        self.worker.api.add_format.assert_called_once_with(
            90, 'srt', str(Path('/path/to/test.srt')), run_hooks=False)
        self.worker.gui.library_view.model.assert_called_once()
        self.worker.gui.library_view.model().books_added \
            .assert_called_once_with(1)
        self.worker.api.format_abspath.assert_called_once_with(90, 'srt')
        self.worker.gui.status_bar.show_message.assert_called_once_with(
            'test description ' + 'completed', 5000)
        self.assertEqual('test custom title [German]', metadata.title)

        arguments = self.worker.gui.proceed_question.mock_calls[0].args
        self.assertIsInstance(arguments[0], Callable)
        self.assertIs(self.worker.gui.job_manager.launch_gui_app, arguments[1])
        self.assertEqual(str(Path('/path/to/log')), arguments[2])
        self.assertEqual('Ebook Translation Log', arguments[3])
        self.assertEqual('Translation Completed', arguments[4])
        self.assertEqual(
            'The translation of "test custom title [German]" was completed. '
            'Do you want to open the book?',
            arguments[5])

        mock_payload = Mock()
        arguments[0](mock_payload)
        mock_open_path.assert_called_once_with(
            str(Path('/path/to/test[m].srt')))

        arguments = self.worker.gui.proceed_question.mock_calls[0].kwargs
        self.assertEqual(True, arguments.get('log_is_file'))
        self.assertIs(self.icon, arguments.get('icon'))

    @patch(module_name + '.open_path')
    @patch(module_name + '.os.rename')
    @patch(module_name + '.open')
    def test_translate_done_other_to_path(
            self, mock_open, mock_os_rename, mock_open_path):
        self.job.failed = False
        self.job.description = 'test description'
        self.job.log_path = str(Path('/path/to/log'))
        metadata_config = {'lang_mark': True}
        self.worker.config = {
            'ebook_metadata': metadata_config,
            'to_library': False,
        }
        self.ebook.is_extra_format.return_value = True
        self.ebook.id = 89
        self.ebook.title = 'test title'
        self.ebook.custom_title = 'test custom title'
        self.ebook.input_format = 'srt'
        self.ebook.output_format = 'srt'
        self.ebook.custom_title = 'test: custom title*'
        self.ebook.target_lang = 'German'
        self.worker.working_jobs = {
            self.job: (self.ebook, str(Path('/path/to/test.srt')))}
        metadata = Mock()
        self.worker.api.get_metadata.return_value = metadata

        self.worker.translate_done(self.job)

        self.worker.api.get_metadata.assert_called_once_with(89)
        mock_os_rename.assert_called_once_with(
            str(Path('/path/to/test.srt')),
            str(Path('/path/to/test_ custom title_ [German].srt')))
        self.worker.gui.status_bar.show_message.assert_called_once_with(
            'test description ' + 'completed', 5000)
        arguments = self.worker.gui.proceed_question.mock_calls[0].args
        self.assertIsInstance(arguments[0], Callable)
        self.assertIs(self.worker.gui.job_manager.launch_gui_app, arguments[1])
        self.assertEqual(str(Path('/path/to/log')), arguments[2])
        self.assertEqual('Ebook Translation Log', arguments[3])
        self.assertEqual('Translation Completed', arguments[4])
        self.assertEqual(
            'The translation of "test: custom title* [German]" was completed. '
            'Do you want to open the book?',
            arguments[5])

        mock_payload = Mock()
        arguments[0](mock_payload)
        mock_open_path.assert_called_once_with(
            str(Path('/path/to/test_ custom title_ [German].srt')))

        arguments = self.worker.gui.proceed_question.mock_calls[0].kwargs
        self.assertEqual(True, arguments.get('log_is_file'))
        self.assertIs(self.icon, arguments.get('icon'))
