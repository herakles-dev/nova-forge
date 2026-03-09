# Static Site

This is a static HTML/CSS website served by nginx.

## Stack
- HTML5
- CSS3 (system fonts, dark mode, mobile-first responsive)
- nginx:alpine (production server)

## Files
- `index.html` — Main page with header, hero, and footer
- `style.css` — Stylesheet with CSS custom properties and dark mode support

## Running locally
Open `index.html` directly in a browser, or use any static file server:
```bash
python3 -m http.server 8080
```

## Running with Docker
```bash
docker build -t static-site .
docker run -p 8080:80 static-site
```

## Customizing
- Edit `index.html` to change page content and structure
- Edit `style.css` to update colors, fonts, and layout
- Add additional pages and link them from `index.html`
