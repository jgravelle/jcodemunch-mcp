table 50100 "My Table"
{
    fields
    {
        field(1; "No."; Code[20])
        {
            Caption = 'No.';
        }
        field(2; Name; Text[100])
        {
            Caption = 'Name';
        }
    }
    keys
    {
        key(PK; "No.")
        {
            Clustered = true;
        }
        key(Name; Name, "No.")
        {
        }
    }
    fieldgroups
    {
        fieldgroup(DropDown; "No.", Name) { }
    }
}

codeunit 50100 "My Codeunit"
{
    procedure MyMethod(Param1: Integer; Param2: Text[100]): Boolean
    var
        LocalVar: Record "My Table";
    begin
        exit(true);
    end;

    local procedure PrivateHelper()
    begin
        // internal logic
    end;

    /// <summary>
    /// Documented procedure with XML doc.
    /// </summary>
    internal procedure DocumentedProc()
    begin
    end;

    // Calculate the total amount for the given record
    procedure CalcTotal(Rec: Record "My Table"): Decimal
    var
        Total: Decimal;
        LineRec: Record "My Table";
    begin
        exit(0);
    end;

    trigger OnRun()
    begin
        MyMethod(1, 'test');
    end;
}

enum 50100 "My Enum"
{
    value(0; None) { }
    value(1; Option1) { Caption = 'Option 1'; }
}

interface "IMyInterface"
{
    procedure DoWork();
    procedure GetValue(): Integer;
}

page 50100 "My Page"
{
    SourceTable = "My Table";
    layout
    {
        area(Content)
        {
            field(Name; Rec.Name) { }
            field("No."; Rec."No.") { }
        }
    }
    actions
    {
        area(Processing)
        {
            // Post the current document
            action(PostDocument)
            {
                Caption = 'Post';
            }
            action("Run Report")
            {
                Caption = 'Run Report';
            }
        }
    }
}

pageextension 50100 "My Page Ext" extends "Customer Card"
{
    layout
    {
        addafter(Name)
        {
            field("Custom Field"; Rec."Custom Field") { }
        }
    }
}

tableextension 50100 "My Table Ext" extends "Customer"
{
    fields
    {
        field(50100; "Custom Field"; Text[100]) { }
    }
}

enumextension 50100 "My Enum Ext" extends "My Enum"
{
    value(10; NewOption) { }
}

codeunit 50101 "Event Subscriber"
{
    [EventSubscriber(ObjectType::Codeunit, Codeunit::"My Codeunit", 'OnBeforePost', '', false, false)]
    local procedure OnBeforePost(var Rec: Record "My Table")
    begin
    end;

    [BusinessEvent(false)]
    procedure OnCustomEvent(Param: Integer)
    begin
    end;

    [IntegrationEvent(false, false)]
    procedure OnIntegrationEvent()
    begin
    end;
}

report 50100 "My Report"
{
    dataset
    {
        dataitem(MyTable; "My Table")
        {
            column(No; "No.") { }
        }
    }
}

query 50100 "My Query"
{
    elements
    {
        dataitem(MyTable; "My Table")
        {
            column(No; "No.") { }
        }
    }
}

xmlport 50100 "My XMLport"
{
    schema
    {
        textelement(Root)
        {
            tableelement(Item; "My Table")
            {
                fieldelement(No; Item."No.") { }
            }
        }
    }
}

controladdin MyAddIn
{
    event OnReady();
    event OnCallback(data: Text);
    procedure Initialize(config: Text);
}
