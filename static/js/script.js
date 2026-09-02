document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.flash').forEach(function (item) {
        setTimeout(function () { item.style.opacity = '0'; item.style.transition = 'opacity .4s'; setTimeout(function(){ item.remove(); }, 400); }, 3500);
    });
});
