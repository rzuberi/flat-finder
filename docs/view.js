// The London site lives at londonflat.xyz; the GitHub Pages copy just forwards there.
if (location.hostname.endsWith("github.io")) location.replace("https://www.londonflat.xyz/");

window.VIEW = {
  key: "london1", title: "London Flat Finder — one bedroom", emoji: "🌿", theme: "cream",
  people: ["Clara"],
  beds_options: [["1", "1 bed"], ["2", "2 beds"], ["0", "Studio"], ["any", "Any beds"]],
  beds_default: "1",
  pmax_default: 2000, price_cap: 3000,
  tt_default: ["Waterloo", "pt", 35],
  links: [["two bedrooms →", "two/"]]
};
