// Opens help in one pop-up window (issue #34). Every help link has data-help and target="rb-help":
// without this script, or with pop-ups blocked, help still opens in a single reused tab named rb-help.
// window.open with the same name returns the window that's already open, so a second click shows
// the requested page in it and brings it to the front instead of opening another one.
(function () {
  "use strict";
  var NAME = "rb-help";
  var FEATURES = "popup,width=900,height=700";

  document.addEventListener("click", function (event) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return; // let the browser handle new-tab and similar clicks
    }
    var link = event.target && event.target.closest ? event.target.closest("a[data-help]") : null;
    if (!link) {
      return;
    }
    var win = window.open(link.href, NAME, FEATURES);
    if (win) {
      event.preventDefault();
      win.focus();
    }
  });
})();
