# Robot Framework Test Suite for ZERO Terminal

*** Settings ***
Documentation    ZERO Terminal Web Interface Tests
Library          SeleniumLibrary
Library          RequestsLibrary

*** Variables ***
${SERVER_URL}    http://localhost:8000
${BROWSER}       Chrome

*** Test Cases ***

Test Login Page
    [Documentation]    Test that login page loads correctly
    Open Browser    ${SERVER_URL}/login    ${BROWSER}
    Page Should Contain    ZERO Terminal Login
    Close Browser

Test Predictions Page
    [Documentation]    Test that predictions page loads and displays data
    Open Browser    ${SERVER_URL}/predictions    ${BROWSER}
    Page Should Contain    NIFTY 50
    Page Should Contain    Quantum Prediction Vector
    # Wait for data to load
    Wait Until Page Contains    LIVE DATA    10s
    Close Browser

Test News Page
    [Documentation]    Test that news page loads and displays articles
    Open Browser    ${SERVER_URL}/news    ${BROWSER}
    Page Should Contain    Market Intelligence
    Wait Until Page Contains Element    article    10s
    Close Browser

Test Trading Page
    [Documentation]    Test that trading page loads and has order form
    Open Browser    ${SERVER_URL}/trading    ${BROWSER}
    Page Should Contain    Quantum Trading Terminal
    Page Should Contain Element    input[placeholder='Quantity']
    Page Should Contain    BUY
    Page Should Contain    SELL
    Close Browser

Test API Health Check
    [Documentation]    Test API health endpoint
    Create Session    api    ${SERVER_URL}
    GET    /health    headers=${HEADERS}
    Should Be Equal As Strings    ${response.status_code}    200
    Should Contain    healthy    ${response.text}
    Close Session    api

Test API Predictions Endpoint
    [Documentation]    Test predictions API endpoint
    Create Session    api    ${SERVER_URL}
    GET    /api/v1/predictions/NIFTY 50    headers=${HEADERS}
    Should Be Equal As Strings    ${response.status_code}    200
    Should Contain    NIFTY 50    ${response.text}
    Close Session    api

Test API News Endpoint
    [Documentation]    Test news API endpoint
    Create Session    api    ${SERVER_URL}
    GET    /api/v1/news    headers=${HEADERS}
    Should Be Equal As Strings    ${response.status_code}    200
    Should Contain    news    ${response.text}
    Close Session    api

Test API Live Prices Endpoint
    [Documentation]    Test live prices API endpoint
    Create Session    api    ${SERVER_URL}
    GET    /api/v1/live-prices    headers=${HEADERS}
    Should Be Equal As Strings    ${response.status_code}    200
    Should Contain    prices    ${response.text}
    Close Session    api

Test WebSocket Connection
    [Documentation]    Test WebSocket connection
    Create WebSocket    ws    ${SERVER_URL.replace('http://', 'ws://')}/ws/market-data
    Send WebSocket Message    ws    ping
    Wait For WebSocket Response    ws    5s
    Close WebSocket    ws

*** Keywords ***

Login With Credentials
    [Documentation]    Login with username and password
    Input Text    id:username    ${USERNAME}
    Input Text    id:password    ${PASSWORD}
    Click Button    Login

Check Connection Status
    [Documentation]    Verify connection status badge is visible
    Wait Until Element Is Visible    .connection-status    10s

*** Variables ***
${HEADERS}    Content-Type: application/json
