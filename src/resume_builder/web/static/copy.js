/* Copy buttons for the rb commands (issue #36).
 * The buttons start hidden and appear only where the clipboard is available (localhost counts as
 * a secure context). Without this script the commands are still plain, selectable text. */
(function () {
  "use strict";
  if (!navigator.clipboard || !navigator.clipboard.writeText) { return; }
  var buttons = document.querySelectorAll("button.copy[data-copy]");
  for (var i = 0; i < buttons.length; i++) { buttons[i].hidden = false; }
  document.addEventListener("click", function (event) {
    var button = event.target && event.target.closest ? event.target.closest("button.copy[data-copy]") : null;
    if (!button) { return; }
    navigator.clipboard.writeText(button.getAttribute("data-copy")).then(function () {
      button.textContent = "Copied";
      setTimeout(function () { button.textContent = "Copy"; }, 1500);
    }, function () {
      button.textContent = "Select and copy";
    });
  });
})();
