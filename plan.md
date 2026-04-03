# What this software does

This software reads articles and summarizes them for the user. It filters out the articles the user would not be interested in.

This software has two inputs

Firstly, it has file called sources.json which tells the system where to look for articles.
The file would be of this form:

[
    {
        category: "News",
        sources: 
        [
            'cnn.com'
            'bbc.co.uk'
        ]
    },
    {
        category: "Science",
        sources:
        [
            'science.com'
        ]
    }
]


Second, it has a file called instructions.md which is written in plain english and tells the software how to filter. i.e. what the user cares about. For example:

"For news, I am interested in local news for Toronto and Ontario and domestic Canadian news. I am not interested in geopolitical news or US politics."


# Architecture

We should take a modular approach to building this software.  We should have these modules

## Fetching Module

This module takes a URL as input.
This URL is assumed to be the "home page" that lists a number of articles. For example, cnn.com or techcrunch.com
The output is a list of articles in the form:

    [
        {
            title:
            date: (if avaialble)
            content: (the text of the article)
            url: the url of the article
        }.
        etc
    ]

### Processing

The module will first pull out all the links. (i.e. <a href..>)
Then we pass that information to an LLM. the LLM will be instructed to produce the output.

## Summarizing Module

This module will take the list of articles from the fetching module and process the list.
It will decide which articles match the section criteria given by the user in instructions.md
For those articles that meet the criteria, it will generate a short 25 word summary and a 200 word summary.
It will output a list in the form:

 [
        {
            title:
            date: (if avaialble)
            content: (the text of the article)
            url: the url of the article
            short_summary:
            full_summary:
        }.
        etc
    ]

## main program

The main program will iterate through all sources.json and generate output that looks like this:

[
    {
        category: "News",
        articles: 
        [
            {
            title:
            date: (if avaialble)
            content: (the text of the article)
            url: the url of the article
            short_summary:
            full_summary:
            },
            etc
        ]
    },
]