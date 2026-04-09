// ============================================================================
// Resume Builder — Server
// Renders a Markdown resume with live preview and PDF export.
// Usage: node server.js [path/to/resume.md]
// Author: Renan Mathias Fernandes (me@renanfernandes.org), 2026
// ============================================================================

const express = require('express');
const { marked } = require('marked');
const fs = require('fs');
const path = require('path');
const chokidar = require('chokidar');
const { WebSocketServer } = require('ws');
const puppeteer = require('puppeteer');

const app = express();
const PORT = 3000;

// Accept an optional CLI argument for the markdown file, default to resume.md
let currentMdFile = process.argv[2] || 'resume.md';
let currentTemplate = 'classic';
const mdFilePath = path.resolve(currentMdFile);

// Serve the frontend and parse JSON request bodies
app.use(express.static(path.join(__dirname, 'public')));
app.use(express.json());

// ---- API Routes ------------------------------------------------------------

// Returns all available template names by scanning the styles directory for CSS files
app.get('/api/templates', (req, res) => {
  const stylesDir = path.join(__dirname, 'public', 'styles');
  const files = fs.readdirSync(stylesDir)
    .filter(f => f.endsWith('.css'))
    .map(f => f.replace('.css', ''));
  res.json(files);
});

// Reads the markdown file, converts it to HTML via `marked`, and returns it
app.get('/api/resume', (req, res) => {
  try {
    const md = fs.readFileSync(mdFilePath, 'utf-8');
    const html = marked(md);
    res.json({ html, file: path.basename(mdFilePath) });
  } catch (err) {
    res.status(500).json({ error: `Could not read ${mdFilePath}: ${err.message}` });
  }
});

// Generates a PDF of the resume using Puppeteer.
// Query param `template` selects which CSS template to apply.
// The sidebar template requires a client-side DOM transformation script
// that restructures the flat HTML into a two-column sidebar layout.
app.get('/api/export-pdf', async (req, res) => {
  const template = req.query.template || 'classic';

  try {
    const md = fs.readFileSync(mdFilePath, 'utf-8');
    const resumeHtml = marked(md);

    // Sanitize template name to prevent path traversal
    const safeName = path.basename(template);
    const cssPath = path.join(__dirname, 'public', 'styles', `${safeName}.css`);

    if (!fs.existsSync(cssPath)) {
      return res.status(400).json({ error: 'Template not found' });
    }

    const css = fs.readFileSync(cssPath, 'utf-8');
    const baseCss = fs.readFileSync(path.join(__dirname, 'public', 'styles', 'base.css'), 'utf-8');

    // The sidebar template needs a post-render DOM transformation:
    // it extracts the name, subtitle, and contact info from the flat HTML
    // and restructures them into a two-column grid (sidebar + main content).
    const sidebarScript = safeName === 'sidebar' ? `
    <script>
      (function() {
        const content = document.querySelector('.resume-content');
        const html = content.innerHTML;
        const parser = new DOMParser();
        const doc = parser.parseFromString('<div>' + html + '</div>', 'text/html');
        const root = doc.body.firstChild;
        const h1 = root.querySelector('h1');
        const name = h1 ? h1.textContent : '';
        let subtitle = '', contactLines = [], linksHtml = '';
        if (h1) {
          let sib = h1.nextElementSibling, idx = 0;
          while (sib && sib.tagName === 'P' && idx < 3) {
            if (idx === 0) subtitle = sib.textContent;
            else if (idx === 1) contactLines = sib.textContent.split('|').map(s => s.trim());
            else linksHtml = sib.innerHTML;
            idx++; sib = sib.nextElementSibling;
          }
        }
        const nameParts = name.split(' ').map(p => '<div>' + p + '</div>').join('');
        const sidebar = '<div class="resume-sidebar">'
          + '<div class="sidebar-name">' + nameParts + '</div>'
          + '<div class="sidebar-title">' + subtitle + '</div>'
          + (linksHtml ? '<div style="margin-bottom:auto;font-size:12px">' + linksHtml + '</div>' : '')
          + '<div class="sidebar-section-label">Contact</div>'
          + contactLines.map(c => '<div class="sidebar-contact-item">' + c + '</div>').join('')
          + '</div>';
        content.innerHTML = sidebar + '<div class="resume-main">' + html + '</div>';
      })();
    <\/script>` : '';

    // Build a full standalone HTML page for Puppeteer to render.
    // Includes Google Fonts used by various templates and the selected CSS.
    const fullHtml = `<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&family=Lora:ital,wght@0,400;0,500;0,600;0,700;1,400;1,500&family=Source+Code+Pro:wght@400;500&display=swap" rel="stylesheet">
  <style>${baseCss}\n${css}</style>
</head>
<body>
  <div class="resume-container">
    <div class="resume-content">${resumeHtml}</div>
  </div>
  ${sidebarScript}
</body>
</html>`;

    // Launch headless Chromium to render the HTML and produce a PDF
    const browser = await puppeteer.launch({
      headless: true,
      args: ['--no-sandbox', '--disable-setuid-sandbox']
    });
    const page = await browser.newPage();
    await page.setContent(fullHtml, { waitUntil: 'networkidle0' });

    // Sidebar template needs zero margins to preserve the edge-to-edge layout
    const pdfMargin = safeName === 'sidebar'
      ? { top: '0', right: '0', bottom: '0', left: '0' }
      : { top: '0.4in', right: '0.4in', bottom: '0.4in', left: '0.4in' };

    const pdf = await page.pdf({
      format: 'Letter',
      printBackground: true,
      margin: pdfMargin
    });

    await browser.close();

    // Send the PDF as a downloadable attachment
    const filename = path.basename(mdFilePath, '.md');
    res.set({
      'Content-Type': 'application/pdf',
      'Content-Disposition': `attachment; filename="${filename}_resume.pdf"`,
      'Content-Length': pdf.length
    });
    res.send(pdf);
  } catch (err) {
    console.error('PDF export error:', err);
    res.status(500).json({ error: `PDF export failed: ${err.message}` });
  }
});

// ---- Server & Live Reload --------------------------------------------------

// Start the Express server and attach WebSocket on the same port
const server = app.listen(PORT, () => {
  console.log(`\n🚀 Resume Builder running at http://localhost:${PORT}`);
  console.log(`📄 Watching: ${mdFilePath}`);
  console.log(`\n   Edit your .md file and see changes live!\n`);
});

// WebSocket server for pushing live updates to connected browsers
const wss = new WebSocketServer({ server });

const broadcast = (data) => {
  wss.clients.forEach(client => {
    if (client.readyState === 1) {
      client.send(JSON.stringify(data));
    }
  });
};

// Watch the markdown file on disk — on every save, re-render and push
// the updated HTML to all connected browser clients via WebSocket
const watcher = chokidar.watch(mdFilePath, { persistent: true });
watcher.on('change', () => {
  try {
    const md = fs.readFileSync(mdFilePath, 'utf-8');
    const html = marked(md);
    broadcast({ type: 'update', html });
    console.log(`♻️  File changed, pushed update`);
  } catch (err) {
    console.error('Watch error:', err);
  }
});
