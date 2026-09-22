// The London site lives at londonflat.xyz; the GitHub Pages copy just forwards there.
if (location.hostname.endsWith("github.io")) location.replace("https://www.londonflat.xyz/two/");

window.VIEW = {
  key: "london", title: "London Flat Finder — two bedrooms", emoji: "🏠",
  data: "../data.json",
  people: ["Rehan", "Clara"],
  beds_options: [["2", "2 beds"], ["1", "1 bed"], ["0", "Studio"], ["3", "3+ beds"], ["any", "Any beds"]],
  beds_default: "2",
  links: [["← one bedroom", "../"]]
};
