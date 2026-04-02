# What this software does

This software reads articles and summarizes them for the user. It filters out the articles the user would not be interested in.

This software has two inputs

Firstly, it has file called sources.json which tells the system where to look for articles
Second, it has a file called instructions.md which is written in plain english and tells the software how to filter. i.e. what the user cares about.



1) a JSON file listing 

# Fundamental Input

THe fundamental input is JSON in the form:

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

Also, there is a instruction prompt that tells the software what information I want.  For example, the instrunction might be:

For news, I am interested in local news for Toronto and Ontario and domestic Canadian news. I am not interested in geopolitical news or US politics.



# Article Fetch module

## Input

a URL. This URL is assumed to be the "home page" that lists a number of articles. For example, cnn.com or techcrunch.com

## Output

a list of articles in the form:
    [
        {
            title:
            date: (if avaialble)
            content: (the text of the article)
        }.
        etc
    ]

## Processing

The module will first pull out all the links. (i.e. <a href..>)
Then we pass that information to an LLM. the LLM will be instructed to produce the output.

