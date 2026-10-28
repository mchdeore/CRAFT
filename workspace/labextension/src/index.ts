// "Preview" on .docx, .xlsx and .pptx files: a PDF tab that redraws after each save.
import { JupyterFrontEnd, JupyterFrontEndPlugin } from '@jupyterlab/application';
import { IFrame, MainAreaWidget } from '@jupyterlab/apputils';
import { PageConfig, URLExt } from '@jupyterlab/coreutils';
import { IDefaultFileBrowser } from '@jupyterlab/filebrowser';

const TYPES = [
  { name: 'craft-docx', extensions: ['.docx'] },
  { name: 'craft-xlsx', extensions: ['.xlsx'] },
  { name: 'craft-pptx', extensions: ['.pptx'] }
];

const plugin: JupyterFrontEndPlugin<void> = {
  id: 'craft-preview:plugin',
  autoStart: true,
  requires: [IDefaultFileBrowser],
  activate: (app: JupyterFrontEnd, browser: IDefaultFileBrowser) => {
    for (const type of TYPES) {
      app.docRegistry.addFileType({ ...type, displayName: type.extensions[0], contentType: 'file', fileFormat: 'base64' });
    }
    app.commands.addCommand('craft:preview', {
      label: 'Preview',
      execute: () => {
        const item = browser.selectedItems().next();
        if (!item || item.done) {
          return;
        }
        open(app, item.value.path);
      }
    });
    for (const type of TYPES) {
      app.contextMenu.addItem({
        command: 'craft:preview',
        selector: `.jp-DirListing-item[data-file-type="${type.name}"]`,
        rank: 1
      });
    }
  }
};

function open(app: JupyterFrontEnd, path: string): void {
  const frame = new IFrame({ sandbox: ['allow-same-origin', 'allow-scripts'] });
  const widget = new MainAreaWidget({ content: frame });
  widget.id = `craft-preview-${path}`;
  widget.title.label = `Preview: ${path.split('/').pop()}`;
  widget.title.closable = true;
  let shown = '';
  const refresh = async () => {
    try {
      const model = await app.serviceManager.contents.get(path, { content: false });
      if (model.last_modified !== shown) {
        shown = model.last_modified;
        frame.url = URLExt.join(PageConfig.getBaseUrl(), 'craft/preview') +
          `?path=${encodeURIComponent(path)}&t=${encodeURIComponent(shown)}`;
      }
    } catch {
      // The file was moved or removed; keep the last preview.
    }
  };
  const timer = window.setInterval(refresh, 1000);
  widget.disposed.connect(() => window.clearInterval(timer));
  void refresh();
  app.shell.add(widget, 'main', { mode: 'split-right' });
}

export default plugin;
