/* global $ */
$(() => {

    $('#analyze').click(() => {
        var inputs;
        inputs[0] = $('#passage_text').text;
        inputs[1] = $('#question_text').text;
        alert(JSON.stringify(inputs));
        $.ajax({
            url: '/api/intqa',
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify(inputs),
            success: (data) => {
                $('#answer').text(data.results);
            }
        });
    });

});
