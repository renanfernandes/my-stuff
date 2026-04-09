# Resume Builder

A live-preview resume renderer that turns a Markdown file into a beautifully styled resume with multiple templates and one-click PDF export.

![Node.js](https://img.shields.io/badge/Node.js-18%2B-green) ![Express](https://img.shields.io/badge/Express-4.x-lightgrey) ![License](https://img.shields.io/badge/License-MIT-blue)

## Features

- **Live Preview** — Edit your `.md` file and see changes instantly in the browser via WebSocket
- **7 Templates** — Switch between styles with one click:
  | Template | Style |
  |----------|-------|
  | Classic | Traditional serif, corporate |
  | Modern | Clean sans-serif, blue/purple gradients |
  | Minimal | Monospace, developer aesthetic |
  | Executive | Dark header band, gold accents |
  | Sidebar | Two-column with dark sidebar |
  | Warm | Cream background, bold indigo accents |
  | LaTeX | Academic paper, Computer Modern feel |
  | GitHub | README.md inspired, familiar to devs |
- **PDF Export** — Generates pixel-perfect PDFs via headless Chromium (Puppeteer)
- **Drop-in Templates** — Add a `.css` file to `public/styles/` and it appears automatically

## Quick Start

```bash
# Install dependencies
npm install

# Start the server (defaults to resume.md in the current directory)
npm start

# Or specify a different markdown file
node server.js path/to/your-resume.md
```

Then open [http://localhost:3000](http://localhost:3000) in your browser.

## Usage

1. Write your resume in Markdown (see `resume.md` for an example)
2. Run the server
3. Pick a template from the toolbar
4. Edit your `.md` file in any editor — changes appear live
5. Click **Export PDF** to download

## Resume Markdown Format

The renderer expects a standard Markdown structure. Here's the general layout:

```markdown
# YOUR NAME
**Your Title**<br>
City, State | Phone | email@example.com<br>
[LinkedIn](https://linkedin.com/in/you) | [Portfolio](https://yoursite.com)

---

### **SECTION HEADING**
Content here...

#### **COMPANY NAME**
**Role Title** | *Dates*
* Bullet point achievements
```

## Project Structure

```
resume_builder/
├── server.js              # Express server, API routes, WebSocket, PDF export
├── package.json
├── resume.md              # Your resume (Markdown)
└── public/
    ├── index.html         # Frontend app (toolbar, live preview, template switcher)
    └── styles/
        ├── base.css       # Shared base styles for all templates
        ├── classic.css
        ├── modern.css
        ├── minimal.css
        ├── executive.css
        ├── sidebar.css
        ├── warm.css
        ├── latex.css
        └── github.css
```

## Adding Custom Templates

1. Create a new CSS file in `public/styles/` (e.g., `mytheme.css`)
2. Style the `.resume-content` and its child elements (`h1`, `h3`, `h4`, `p`, `ul`, `li`, `hr`, etc.)
3. Restart the server — your template appears in the toolbar automatically

## Tech Stack

- **Express** — HTTP server and API
- **Marked** — Markdown to HTML conversion
- **Chokidar** — File watcher for live reload
- **WebSocket (ws)** — Push updates to the browser
- **Puppeteer** — Headless Chromium for PDF generation

## Author

**Renan Fernandes** — [me@renanfernandes.org](mailto:me@renanfernandes.org)

## License

MIT
